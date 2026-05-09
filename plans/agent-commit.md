# Agent Commit — Version Control for AI Reasoning

## Goal

Give every AI agent reasoning step a stable, queryable, reusable ID — like git commits, but for agent cognition. Built as an MCP server so any agent framework (Hermes, Claude Code, Codex, etc.) gets it for free.

## Problem

Every AI agent run is ephemeral. When Agent A finishes a subtask, Agent B has to re-derive the same context from scratch. Multi-agent workflows pay massive redundant reasoning costs. No way to:

- Reference "continue from where agent X left off in task Y"
- Branch a new agent off an existing reasoning path
- Diff two reasoning paths to see what changed
- Audit the full decision chain for compliance or debugging

## What's Already Crowded

Shared memory stores: `omem`, `openmemory`, `vancelin/stash`, `plur`, `friday-studio`. These are content stores. They don't version the reasoning *process*.

**Found during research:**
- `agentstateprotocol` (0 stars) — compliance-focused checkpoint protocol, regulated AI market
- `reasoning-replay` (0 stars) — visual replay only, no versioning

Neither does versioning of agent cognition steps.

## What Agent Commit Does

### Core Model

```
Commit = {
  id: content_hash,          # sha256 of (messages + tool_calls + result)
  parent_id: hash | null,    # previous commit
  timestamp: ISO8601,
  agent_id: str,
  task_id: str,
  messages: [...],           # full context at this step
  tool_calls: [...],        # what was called
  result: {...},            # tool results
  reasoning: str | null,    # if model emitted reasoning
  metadata: {...}           # token count, model, provider, etc.
}
```

### Operations

1. **commit** — record a reasoning step with full context, get a content-hashed ID
2. **log** — show commit chain for a task, like `git log`
3. **diff** — show what changed in context between two commits
4. **branch** — create a new commit chain from an existing commit (fork)
5. **merge** — if two branches converge, record the merge point
6. **reference** — given a commit ID, retrieve full reasoning context to inject into a new agent session

### MCP Server Interface

```
mcp__agent_commit__commit(messages, tool_calls, result, agent_id, task_id, metadata)
  → { id, parent_id, timestamp }

mcp__agent_commit__log(task_id)
  → [{ id, parent_id, timestamp, summary }]

mcp__agent_commit__diff(commit_a_id, commit_b_id)
  → { added, removed, changed }

mcp__agent_commit__branch(commit_id, new_task_id)
  → { id, branched_from }

mcp__agent_commit__reference(commit_id)
  → { messages, tool_calls, reasoning }

mcp__agent_commit__merge(commit_a_id, commit_b_id, resolution)
  → { id, merged_from: [a, b] }
```

### Storage Backends

- **SQLite** (default) — local single-user, `~/.hermes/agent_commit.db`
- **PostgreSQL** — team/shared, connection string in config
- **S3** — archival, immutable blob store

### Integration Points

- Hermes tools: `commit_reasoning()`, `agent_commit_log()`, `agent_commit_diff()`
- MCP server: standalone `hermes mcp serve` or embedded
- Session hooks: auto-commit on tool call boundaries (configurable)
- No native dependency on any specific agent framework

## Files to Create

### New files
1. `tools/agent_commit_tool.py` — Core commit/diff/branch/merge logic (~350 lines)
2. `mcp/agent_commit_server.py` — MCP server implementation (~200 lines)
3. `agent/commit_store.py` — SQLite + PostgreSQL + S3 storage adapters (~250 lines)
4. `agent/commit_id.py` — Content hashing, ID generation (~50 lines)
5. `tools/registry.py` — Register tool (auto-discovery pattern)
6. `tests/tools/test_agent_commit_tool.py` — Unit tests (~150 lines)
7. `tests/mcp/test_agent_commit_server.py` — MCP integration tests (~100 lines)

### Modifications
8. `toolsets.py` — Add `agent_commit` to toolsets
9. `mcp_serve.py` — Mount agent_commit server routes
10. `hermes_cli/commands.py` — Add `/commit` slash command
11. `config.yaml.example` — Add `agent_commit` section

## Estimated scope
~850 lines new code, ~100 lines modifications, ~250 lines tests = ~1,200 lines total

## Validation

- Run full test suite: `python -m pytest tests/ -o 'addopts=' -q`
- Manual: start Hermes, run a task, commit it, retrieve by ID, verify context matches

## Why Hermes?

The MCP server pattern maps directly to Hermes's architecture. The commit store fits in `~/.hermes/`. The tool registers via the same auto-discovery pattern every tool uses. And Hermes's existing cron/session/health infra makes it a natural home for "agent commit" as a first-class feature rather than a standalone sidecar.