# Agent Commit

**Version control for AI reasoning.**

Every LLM turn — messages, tool calls, results, reasoning — gets a content-addressed, queryable commit ID. Like git, but for what your agent is thinking.

```
pip install agent-commit
```

---

## The problem

**Agents re-derive the same context over and over.** Agent A figures out the project structure, then Agent B starts from scratch and does it again. With 5 agents sharing a task, that's 5x the reasoning cost for the same work. Even a single long-running session pays this tax — context window fills up, summarization kicks in, history gets lost.

**Reasoning is ephemeral.** Once a conversation ends, the chain of thinking is gone. You can't ask "what was Agent A's reasoning on step 3?" — it's buried in a flattened transcript with no structure.

**No branching means no experimentation.** Want to try a different approach? Restart the whole session or manually piece together context — no way to fork, compare, or merge reasoning paths.

---

## How it works

Agent Commit records every agent turn as an immutable commit with a SHA-256[:16] ID. Commits form a parent chain — giving you a browsable history, instant equivalence checks, and branching/merging for free.

```python
from agent_commit import agent_commit_tool

# Record a turn
result = agent_commit_tool(
    action="commit",
    messages=[{"role": "user", "content": "Build a REST API"}],
    tool_calls=[{"id": "1", "name": "terminal", "arguments": {"command": "pip install fastapi"}}],
    tool_results=[{"id": "1", "result": "Installed fastapi-0.115.0"}],
    reasoning="Start with the scaffold, then add auth",
    metadata={"model": "claude-sonnet-4", "turn": 1},
    agent_id="agent-1",
    task_id="build-rest-api",
)
commit_id = json.loads(result)["id"]  # e.g. "a1b2c3d4e5f6g7h8"

# New agent asks: "what was the last reasoning state?"
ctx = agent_commit_tool(action="reference", commit_id=commit_id)

# Compare two paths
diff = agent_commit_tool(action="diff", commit_a_id="a1b2c3d4", commit_b_id="b2c3d4e5")

# Fork a new approach from an existing commit
branched = agent_commit_tool(
    action="branch",
    commit_id=commit_id,
    new_task_id="build-graphql-api",
)
```

---

## What you get

**Reference** — Inject the full reasoning state from any prior commit into a new session. No re-derivation cost.

**Diff** — See exactly what changed between two commits: tools added/removed, message count delta, reasoning drift.

**Branch** — Fork from any commit to try an alternative approach. Parent chain stays intact; both paths are explorable.

**Audit log** — Full timestamp, agent ID, and tool/message state for every turn. Replay or hand off to a human.

---

## MCP server

Works with any MCP-compatible agent — Claude Code, Codex, OpenCode, Hermes, and more — via the built-in stdio adapter:

```json
{
  "mcpServers": {
    "agent-commit": {
      "command": "python",
      "args": ["-m", "agent_commit.mcp_server"]
    }
  }
}
```

```bash
python -m agent_commit.mcp_server
```

No accounts, no external services. SQLite on disk by default.

---

## Project structure

```
src/agent_commit/
├── core.py          # CommitStore, tool handler, schema
├── mcp_server.py    # MCP stdio adapter
└── __init__.py      # Public exports

tests/
└── test_agent_commit.py   # 24 passing tests

pyproject.toml      # zero external dependencies
```

- **Storage:** SQLite at `~/.agent_commit/agent_commit.db` (or `$AGENT_COMMIT_HOME`)
- **IDs:** SHA-256 of canonical turn serialization, truncated to 16 chars
- **Thread-safe:** RLock per store, WAL mode
- **Dependencies:** Python standard library only