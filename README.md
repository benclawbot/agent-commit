# Agent Commit

**Version control for AI reasoning.**

Every LLM turn + tool call becomes a content-addressed commit with a stable, queryable ID — like git commits, but for agent cognition.

```bash
pip install agent-commit
```

---

## The Problem

### Redundant reasoning burns money and time

In a team of agents — or even a single agent with a long session — the same context gets re-derived over and over. Agent A spends 30 seconds figuring out the project structure. Agent B, brought in to help, spends another 30 seconds reaching the same conclusion. Agent C does it again.

With longer conversations, this compounds. A 10-turn reasoning chain that's partially useful to a new agent means either:
- **Repeating the work** — pay the full token cost to re-derive it
- **Shoehorning it into context** — stuff the entire conversation history into the context window, at massive token cost

### Reasoning is ephemeral

Current agents have no memory of *how* they reached a conclusion — only the final output. When a later agent needs to understand prior reasoning, they get a flattened transcript with no structure, no diff, no way to ask "what changed between attempt 3 and attempt 7?"

### No branching, no experimentation

In code, you branch off a stable commit to experiment. In AI workflows, if you want to try a different approach, you either restart the whole conversation or manually piecemeal context together — with no way to compare the two paths side by side.

---

## The Solution

Agent Commit is a persistent, queryable ledger of every reasoning step an agent takes.

Every turn is a **commit**: the messages, tool calls, tool results, and reasoning text are serialized, hashed (SHA-256), and stored with a parent reference — forming a chain you can query, branch, and diff.

```
User: "Build a REST API"
↓
Agent reasoning: "Start with FastAPI scaffold..."
  Tool: terminal → "pip install fastapi uvicorn"
  Tool: terminal → "uvicorn main:app"
↓
Commit abc123def456 [sha256[:16]] — "Start with the server scaffold"
  parent: null
  tools: [terminal]
  messages: 2
  turn: 1

User: "Add JWT auth"
↓
Agent reasoning: "Add auth middleware..."
  Tool: terminal → "pip install python-jose"
  Tool: terminal → "added /auth/login endpoint"
↓
Commit fedcba098765 — "Add JWT auth"
  parent: abc123def456
  tools: [terminal]
  messages: 4
  turn: 2
```

### What you can do with it

**Reference** — Before starting a new sub-agent or session, query the store for relevant prior commits and inject their full context. No re-derivation.

```python
# New agent can ask: "What's the latest reasoning about auth?"
ctx = agent_commit_tool(
    action="reference",
    commit_id="abc123def456",
)
# Returns full messages + tool_calls + tool_results + reasoning
```

**Diff** — Compare two commits to see exactly what changed: which tools were added, which messages were introduced.

```python
diff = agent_commit_tool(
    action="diff",
    commit_a_id="abc123def456",
    commit_b_id="fedcba098765",
)
# → {added_tools: ["terminal"], removed_tools: [], message_count: {a:2, b:4}}
```

**Branch** — Fork a new reasoning chain from any existing commit to try an alternative approach, without destroying the original path.

```python
# Try a GraphQL approach from the REST API commit
branched = agent_commit_tool(
    action="branch",
    commit_id="abc123def456",       # last REST commit
    new_task_id="build-graphql-api",
    new_agent_id="agent-2",
)
# New commit chain, parent linked, original untouched
```

**Audit log** — Every commit is timestamped, has an agent ID, and records the full tool+message state. You can replay, audit, or hand off reasoning to a human.

---

## How it works

### Content-addressed IDs

The commit ID is `sha256(canonical_message)[:16]` — deterministic and deduplicatable. If two agents independently reach the same reasoning state, they get the same ID. No duplicate storage, instant equivalence check.

### Parent chain

Every commit (except the first) stores its parent's ID, forming a DAG. This gives you:
- Full history traversal (like `git log`)
- Branch/merge via parent reassignment
- Replay from any point

### MCP-native

The MCP server adapter means any MCP client can use Agent Commit as a tool, with zero framework lock-in:

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

No config files, no external services, no team accounts. SQLite on disk, or swap in Postgres/S3 for team sharing.

---

## Concepts

- **Commit** — A single reasoning step: messages + tool calls + tool results + reasoning text, hashed with sha256[:16]
- **Task** — A project or conversation thread. Commits are grouped by `task_id`
- **Branch** — Fork a new reasoning chain from any existing commit
- **Reference** — Inject full context from a previous commit into a new session

## Usage

```python
from agent_commit import agent_commit_tool

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

# Get full context from a commit
ctx = agent_commit_tool(action="reference", commit_id=commit_id)

# Diff two commits
diff = agent_commit_tool(action="diff", commit_a_id="abc123", commit_b_id="def456")

# Branch off to try a different approach
branched = agent_commit_tool(
    action="branch",
    commit_id=commit_id,
    new_task_id="build-graphql-api",
    new_agent_id="agent-2",
)
```

## Structure

```
src/agent_commit/
├── core.py          # CommitStore, agent_commit_tool(), AGENT_COMMIT_SCHEMA
├── mcp_server.py    # MCP stdio adapter (works with any MCP client)
└── __init__.py      # Public API

tests/
└── test_agent_commit.py  # 24 tests — hash, store, branching, schema

pyproject.toml  # Zero external dependencies
```

- **Storage:** SQLite at `~/.agent_commit/agent_commit.db` (or `$AGENT_COMMIT_HOME`)
- **Hash:** SHA-256 of canonical serialization, truncated to 16 chars
- **Thread-safe:** RLock per store, WAL mode SQLite
- **No dependencies** beyond the Python standard library
