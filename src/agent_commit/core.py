#!/usr/bin/env python3
"""
Agent Commit — Version control for AI reasoning.

Every LLM turn + tool call becomes a content-addressed commit with a stable ID,
parent chain, and full context. Supports: commit, log, diff, branch, reference, get.

Built as a standalone MCP server and Python library. No Hermes dependency.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

DEFAULT_DB_NAME = "agent_commit.db"


@dataclass
class Commit:
    """A single reasoning step — equivalent to a git commit."""
    id: str                       # sha256[:16] of canonical content
    parent_id: Optional[str]
    agent_id: str
    task_id: str
    timestamp: str                 # ISO8601
    messages: List[Dict]          # full message list at this step
    tool_calls: List[Dict]        # tool calls made
    tool_results: List[Dict]      # results returned
    reasoning: Optional[str]      # chain-of-thought / scratch
    metadata: Dict[str, Any]       # model, provider, token counts, etc.
    summary: str                  # auto-generated one-liner

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict) -> Commit:
        return cls(**d)


# ---------------------------------------------------------------------------
# Content hashing
# ---------------------------------------------------------------------------

def _canonical_payload(
    messages: List[Dict],
    tool_calls: List[Dict],
    tool_results: List[Dict],
    reasoning: Optional[str],
    metadata: Dict[str, Any],
) -> bytes:
    """Canonical serialization for content hashing."""
    canonical = {
        "messages": messages,
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "reasoning": reasoning,
        "metadata": metadata,
    }
    return json.dumps(canonical, sort_keys=True, ensure_ascii=False).encode("utf-8")


def compute_commit_id(
    messages: List[Dict],
    tool_calls: List[Dict],
    tool_results: List[Dict],
    reasoning: Optional[str],
    metadata: Dict[str, Any],
) -> str:
    """Compute a sha256[:16] content hash for a reasoning step."""
    data = _canonical_payload(messages, tool_calls, tool_results, reasoning, metadata)
    return hashlib.sha256(data).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Auto-summary
# ---------------------------------------------------------------------------

def generate_summary(messages: List[Dict], tool_calls: List[Dict]) -> str:
    """Auto-generate a one-line summary for a commit."""
    if messages:
        last = messages[-1]
        content = last.get("content", "")
        if isinstance(content, str) and content.strip():
            first_line = content.strip().split("\n")[0][:80]
            return first_line
    if tool_calls:
        names = [tc.get("name", "?") for tc in tool_calls[:3]]
        return "tool: " + ", ".join(names)
    return "(no content)"


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _get_db_path() -> Path:
    home = Path(os.environ.get("AGENT_COMMIT_HOME", str(Path.home() / ".agent_commit")))
    return home / DEFAULT_DB_NAME


def _init_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Initialize the SQLite database, creating tables if needed."""
    path = db_path or _get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS commits (
            id          TEXT PRIMARY KEY,
            parent_id   TEXT,
            agent_id    TEXT NOT NULL,
            task_id     TEXT NOT NULL,
            timestamp   TEXT NOT NULL,
            messages    TEXT NOT NULL,
            tool_calls  TEXT NOT NULL,
            tool_results TEXT NOT NULL,
            reasoning   TEXT,
            metadata    TEXT NOT NULL,
            summary     TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_task_id ON commits(task_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_id ON commits(agent_id)")
    conn.commit()
    return conn


class CommitStore:
    """Thread-safe commit store backed by SQLite."""

    def __init__(self, db_path: Optional[Path] = None):
        self._lock = threading.RLock()
        self._conn = _init_db(db_path)

    def commit(
        self,
        messages: List[Dict],
        tool_calls: List[Dict],
        tool_results: List[Dict],
        reasoning: Optional[str],
        metadata: Dict[str, Any],
        agent_id: str,
        task_id: str,
        parent_id: Optional[str] = None,
    ) -> Commit:
        """Record a new commit and return it."""
        commit_id = compute_commit_id(messages, tool_calls, tool_results, reasoning, metadata)
        timestamp = datetime.now(timezone.utc).isoformat()
        summary = generate_summary(messages, tool_calls)

        commit = Commit(
            id=commit_id,
            parent_id=parent_id,
            agent_id=agent_id,
            task_id=task_id,
            timestamp=timestamp,
            messages=messages,
            tool_calls=tool_calls,
            tool_results=tool_results,
            reasoning=reasoning,
            metadata=metadata,
            summary=summary,
        )

        with self._lock:
            self._conn.execute("""
                INSERT OR IGNORE INTO commits
                (id, parent_id, agent_id, task_id, timestamp,
                 messages, tool_calls, tool_results, reasoning, metadata, summary)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                commit.id,
                commit.parent_id,
                commit.agent_id,
                commit.task_id,
                commit.timestamp,
                json.dumps(commit.messages, ensure_ascii=False),
                json.dumps(commit.tool_calls, ensure_ascii=False),
                json.dumps(commit.tool_results, ensure_ascii=False),
                json.dumps(commit.reasoning, ensure_ascii=False) if commit.reasoning else None,
                json.dumps(commit.metadata, ensure_ascii=False),
                commit.summary,
            ))
            self._conn.commit()

        return commit

    def get_commit(self, commit_id: str) -> Optional[Commit]:
        """Retrieve a commit by ID."""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM commits WHERE id = ?", (commit_id,)
            )
            row = cursor.fetchone()
        if not row:
            return None
        return self._row_to_commit(row)

    def log(self, task_id: str, limit: int = 50) -> List[Commit]:
        """Return commit chain for a task, newest first."""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM commits WHERE task_id = ? ORDER BY timestamp DESC LIMIT ?",
                (task_id, limit),
            )
            rows = cursor.fetchall()
        return [self._row_to_commit(r) for r in rows]

    def diff(self, commit_a_id: str, commit_b_id: str) -> Dict[str, Any]:
        """Show what changed between two commits."""
        a = self.get_commit(commit_a_id)
        b = self.get_commit(commit_b_id)
        if not a or not b:
            return {"error": "commit not found"}

        a_tools = {tc.get("id", tc.get("name")): tc for tc in a.tool_calls}
        b_tools = {tc.get("id", tc.get("name")): tc for tc in b.tool_calls}

        return {
            "a": {"id": a.id, "timestamp": a.timestamp, "n_messages": len(a.messages)},
            "b": {"id": b.id, "timestamp": b.timestamp, "n_messages": len(b.messages)},
            "tool_calls": {
                "added":   [b_tools[k] for k in b_tools if k not in a_tools],
                "removed": [a_tools[k] for k in a_tools if k not in b_tools],
                "changed": [
                    {"before": a_tools[k], "after": b_tools[k]}
                    for k in a_tools if k in b_tools and a_tools[k] != b_tools[k]
                ],
            },
            "reasoning_a": a.reasoning,
            "reasoning_b": b.reasoning,
        }

    def branch(
        self,
        commit_id: str,
        new_task_id: str,
        new_agent_id: str,
    ) -> Optional[Commit]:
        """Fork a new commit chain from an existing commit."""
        parent = self.get_commit(commit_id)
        if not parent:
            return None

        new_metadata = {**parent.metadata, "branched_from": commit_id}
        commit_id_new = compute_commit_id(
            parent.messages, parent.tool_calls, parent.tool_results,
            parent.reasoning, new_metadata,
        )
        timestamp = datetime.now(timezone.utc).isoformat()

        commit = Commit(
            id=commit_id_new,
            parent_id=commit_id,
            agent_id=new_agent_id,
            task_id=new_task_id,
            timestamp=timestamp,
            messages=parent.messages,
            tool_calls=parent.tool_calls,
            tool_results=parent.tool_results,
            reasoning=parent.reasoning,
            metadata=new_metadata,
            summary=f"[branch from {commit_id[:8]}] {parent.summary}",
        )

        with self._lock:
            self._conn.execute("""
                INSERT OR IGNORE INTO commits
                (id, parent_id, agent_id, task_id, timestamp,
                 messages, tool_calls, tool_results, reasoning, metadata, summary)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                commit.id, commit.parent_id, commit.agent_id, commit.task_id,
                commit.timestamp,
                json.dumps(commit.messages, ensure_ascii=False),
                json.dumps(commit.tool_calls, ensure_ascii=False),
                json.dumps(commit.tool_results, ensure_ascii=False),
                json.dumps(commit.reasoning, ensure_ascii=False) if commit.reasoning else None,
                json.dumps(commit.metadata, ensure_ascii=False),
                commit.summary,
            ))
            self._conn.commit()

        return commit

    def reference(self, commit_id: str) -> Optional[Dict[str, Any]]:
        """Get full context from a commit for injection into a new session."""
        commit = self.get_commit(commit_id)
        if not commit:
            return None
        return {
            "messages": commit.messages,
            "tool_calls": commit.tool_calls,
            "tool_results": commit.tool_results,
            "reasoning": commit.reasoning,
            "metadata": commit.metadata,
            "summary": commit.summary,
        }

    def _row_to_commit(self, row) -> Commit:
        if hasattr(row, "keys"):
            return Commit(
                id=row["id"], parent_id=row["parent_id"],
                agent_id=row["agent_id"], task_id=row["task_id"],
                timestamp=row["timestamp"],
                messages=json.loads(row["messages"]),
                tool_calls=json.loads(row["tool_calls"]),
                tool_results=json.loads(row["tool_results"]),
                reasoning=json.loads(row["reasoning"]) if row["reasoning"] else None,
                metadata=json.loads(row["metadata"]),
                summary=row["summary"],
            )
        cols = [c[0] for c in self._conn.execute("PRAGMA table_info(commits)").fetchall()]
        d = dict(zip(cols, row))
        return Commit.from_dict(d)


# ---------------------------------------------------------------------------
# Singleton store
# ---------------------------------------------------------------------------

_commit_store: Optional[CommitStore] = None
_store_lock = threading.Lock()


def get_store() -> CommitStore:
    global _commit_store
    if _commit_store is None:
        with _store_lock:
            if _commit_store is None:
                _commit_store = CommitStore()
    return _commit_store


# ---------------------------------------------------------------------------
# Tool handler — matches the Hermes tool-calling interface
# ---------------------------------------------------------------------------

def agent_commit_tool(
    action: str,
    messages: Optional[List[Dict]] = None,
    tool_calls: Optional[List[Dict]] = None,
    tool_results: Optional[List[Dict]] = None,
    reasoning: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    agent_id: str = "default",
    task_id: str = "",
    parent_id: Optional[str] = None,
    commit_id: Optional[str] = None,
    commit_a_id: Optional[str] = None,
    commit_b_id: Optional[str] = None,
    new_task_id: Optional[str] = None,
    new_agent_id: Optional[str] = None,
    limit: int = 50,
    **kwargs,
) -> str:
    """Main tool handler for agent_commit operations.

    Args:
        action: One of commit, log, diff, branch, reference, get
        messages: Full message list at this step (for commit)
        tool_calls: Tool calls made this step (for commit)
        tool_results: Tool results this step (for commit)
        reasoning: Chain-of-thought / scratch notes (for commit)
        metadata: Extra context — model, provider, token counts (for commit)
        agent_id: Identifier for the agent creating this commit
        task_id: Task/project this commit belongs to
        parent_id: Parent commit ID for chaining
        commit_id: Commit ID for get/branch/reference
        commit_a_id: First commit for diff
        commit_b_id: Second commit for diff
        new_task_id: Task ID for branched commit
        new_agent_id: Agent ID for branched commit
        limit: Max commits to return for log

    Returns:
        JSON string with the result.
    """
    store = get_store()

    if action == "commit":
        if not task_id:
            return json.dumps({"error": "task_id required for commit"})
        commit = store.commit(
            messages=messages or [],
            tool_calls=tool_calls or [],
            tool_results=tool_results or [],
            reasoning=reasoning,
            metadata=metadata or {},
            agent_id=agent_id,
            task_id=task_id,
            parent_id=parent_id,
        )
        return json.dumps({
            "id": commit.id,
            "parent_id": commit.parent_id,
            "timestamp": commit.timestamp,
            "summary": commit.summary,
        }, indent=2)

    elif action == "log":
        if not task_id:
            return json.dumps({"error": "task_id required for log"})
        commits = store.log(task_id, limit=limit)
        return json.dumps({
            "task_id": task_id,
            "count": len(commits),
            "commits": [
                {
                    "id": c.id,
                    "parent_id": c.parent_id,
                    "timestamp": c.timestamp,
                    "agent_id": c.agent_id,
                    "summary": c.summary,
                }
                for c in commits
            ]
        }, indent=2)

    elif action == "diff":
        if not commit_a_id or not commit_b_id:
            return json.dumps({"error": "commit_a_id and commit_b_id required for diff"})
        return json.dumps(store.diff(commit_a_id, commit_b_id), indent=2)

    elif action == "branch":
        if not commit_id or not new_task_id:
            return json.dumps({"error": "commit_id and new_task_id required for branch"})
        commit = store.branch(commit_id, new_task_id, new_agent_id or agent_id)
        if not commit:
            return json.dumps({"error": f"commit not found: {commit_id}"})
        return json.dumps({
            "id": commit.id,
            "branched_from": commit.parent_id,
            "task_id": commit.task_id,
            "timestamp": commit.timestamp,
            "summary": commit.summary,
        }, indent=2)

    elif action == "reference":
        if not commit_id:
            return json.dumps({"error": "commit_id required for reference"})
        result = store.reference(commit_id)
        if not result:
            return json.dumps({"error": f"commit not found: {commit_id}"})
        return json.dumps(result, indent=2)

    elif action == "get":
        if not commit_id:
            return json.dumps({"error": "commit_id required for get"})
        commit = store.get_commit(commit_id)
        if not commit:
            return json.dumps({"error": f"commit not found: {commit_id}"})
        return json.dumps(commit.to_dict(), indent=2)

    else:
        valid = ["commit", "log", "diff", "branch", "reference", "get"]
        return json.dumps({
            "error": f"unknown action: {action}",
            "valid_actions": valid,
        })


# ---------------------------------------------------------------------------
# OpenAI Function-Calling schema
# ---------------------------------------------------------------------------

AGENT_COMMIT_SCHEMA = {
    "name": "agent_commit",
    "description": (
        "Version control for AI reasoning steps. Record, retrieve, branch, and diff "
        "agent reasoning commits.\n\n"
        "Actions:\n"
        "  commit    — record a reasoning step, get a content-hashed ID\n"
        "  log       — show commit chain for a task (like git log)\n"
        "  diff      — show what changed between two commits\n"
        "  branch    — fork a new reasoning chain from an existing commit\n"
        "  reference — retrieve full context from a commit for injection into a new session\n"
        "  get       — retrieve a single commit by ID"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["commit", "log", "diff", "branch", "reference", "get"],
                "description": "The operation to perform",
            },
            "messages": {
                "type": "array",
                "description": "Full message list at this reasoning step (for commit)",
            },
            "tool_calls": {
                "type": "array",
                "description": "Tool calls made in this step (for commit)",
            },
            "tool_results": {
                "type": "array",
                "description": "Tool results returned in this step (for commit)",
            },
            "reasoning": {
                "type": "string",
                "description": "Model reasoning / chain-of-thought from this step (for commit)",
            },
            "metadata": {
                "type": "object",
                "description": "Additional context: model, provider, token counts, turn_number, etc.",
            },
            "agent_id": {
                "type": "string",
                "description": "Identifier for the agent creating this commit",
                "default": "default",
            },
            "task_id": {
                "type": "string",
                "description": "Task / project this commit belongs to",
            },
            "parent_id": {
                "type": "string",
                "description": "Parent commit ID for chaining (for commit)",
            },
            "commit_id": {
                "type": "string",
                "description": "Commit ID for get / branch / reference actions",
            },
            "commit_a_id": {
                "type": "string",
                "description": "First commit ID for diff",
            },
            "commit_b_id": {
                "type": "string",
                "description": "Second commit ID for diff",
            },
            "new_task_id": {
                "type": "string",
                "description": "Task ID for branched commit",
            },
            "new_agent_id": {
                "type": "string",
                "description": "Agent ID for branched commit",
            },
            "limit": {
                "type": "integer",
                "description": "Max commits to return for log",
                "default": 50,
            },
        },
        "required": ["action"],
    },
}