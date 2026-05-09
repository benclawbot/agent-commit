# Agent Commit

**Version control for AI reasoning.**

Every LLM turn + tool call becomes a content-addressed commit with a stable, queryable ID — like git commits, but for agent cognition.

```
$ pip install agent-commit
```

## Why

Multi-agent workflows pay massive redundant reasoning costs — Agent B re-derives context Agent A already figured out. Agent Commit gives agents a shared, queryable reasoning ledger they can reference, branch, and diff.

## Concepts

- **Commit** — A single reasoning step: messages + tool calls + tool results + reasoning + metadata, hashed with sha256[:16]
- **Task** — A project or conversation thread. Commits are grouped by `task_id`
- **Branch** — Fork a new reasoning chain from any existing commit
- **Reference** — Inject full context from a previous commit into a new session

## Usage

```python
from agent_commit import agent_commit_tool, CommitStore

# Record a reasoning step
result = agent_commit_tool(
    action="commit",
    messages=[{"role": "user", "content": "Build a REST API"}],
    tool_calls=[{"id": "1", "name": "terminal", "arguments": {"command": "fastapi dev"}}],
    tool_results=[{"id": "1", "result": "Server running on :8000"}],
    reasoning="Start with the server scaffold, then add auth routes",
    metadata={"model": "claude-sonnet-4", "provider": "anthropic", "turn": 1},
    agent_id="agent-1",
    task_id="build-rest-api",
)
commit_id = json.loads(result)["id"]

# List all commits for a task
log = agent_commit_tool(action="log", task_id="build-rest-api")

# Get full context from a commit for a new session
ctx = agent_commit_tool(action="reference", commit_id=commit_id)

# Diff two commits
diff = agent_commit_tool(action="diff", commit_a_id="abc123", commit_b_id="def456")

# Fork a new reasoning chain from an existing commit
branched = agent_commit_tool(
    action="branch",
    commit_id=commit_id,
    new_task_id="build-graphql-api",
    new_agent_id="agent-2",
)
```

## MCP Server

Agent Commit ships as an MCP server so any MCP-compatible agent (Claude Code, Codex, OpenCode, Hermes, etc.) can use it with zero configuration:

```json
{
  "mcpServers": {
    "agent-commit": {
      "command": "python",
      "args": ["-m", "agent_commit.mcp_server"],
      "env": {
        "AGENT_COMMIT_HOME": "~/.agent_commit"
      }
    }
  }
}
```

Run it directly:
```bash
python -m agent_commit.mcp_server
```

## Structure

```
src/agent_commit/
├── core.py          # Core library — CommitStore, tool handler, schema
├── mcp_server.py    # MCP stdio server adapter
└── __init__.py      # Public API

tests/
└── test_agent_commit.py     # 24 tests (hash, store, branching, schema)

pyproject.toml  # Package config, no external dependencies
```

- **Storage:** SQLite at `~/.agent_commit/agent_commit.db` (or `$AGENT_COMMIT_HOME`)
- **Hash:** sha256 of canonical (messages + tool_calls + tool_results + reasoning + metadata), truncated to 16 chars
- **Thread-safe:** RLock per store, WAL mode SQLite
- **No dependencies** beyond the Python standard library