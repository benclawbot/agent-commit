"""Tests for agent_commit core library."""

import json
import tempfile
from pathlib import Path

import pytest

from agent_commit import (
    CommitStore,
    compute_commit_id,
    generate_summary,
    agent_commit_tool,
    AGENT_COMMIT_SCHEMA,
)


class TestComputeCommitId:
    def test_deterministic_same_input(self):
        messages = [{"role": "user", "content": "hello"}]
        h1 = compute_commit_id(messages, [], [], None, {})
        h2 = compute_commit_id(messages, [], [], None, {})
        assert h1 == h2

    def test_different_content_different_hash(self):
        h1 = compute_commit_id([{"role": "user", "content": "hello"}], [], [], None, {})
        h2 = compute_commit_id([{"role": "user", "content": "world"}], [], [], None, {})
        assert h1 != h2

    def test_order_matters(self):
        h1 = compute_commit_id([{"a": 1}, {"a": 2}], [], [], None, {})
        h2 = compute_commit_id([{"a": 2}, {"a": 1}], [], [], None, {})
        assert h1 != h2

    def test_tool_calls_included_in_hash(self):
        h1 = compute_commit_id([], [{"id": "1", "name": "write_file"}], [], None, {})
        h2 = compute_commit_id([], [{"id": "2", "name": "read_file"}], [], None, {})
        assert h1 != h2

    def test_metadata_included_in_hash(self):
        h1 = compute_commit_id([], [], [], None, {"model": "claude"})
        h2 = compute_commit_id([], [], [], None, {"model": "gpt-4"})
        assert h1 != h2


class TestGenerateSummary:
    def test_from_last_message_content(self):
        messages = [
            {"role": "user", "content": "Build an API"},
            {"role": "assistant", "content": "I'll create the FastAPI app"},
        ]
        summary = generate_summary(messages, [])
        assert "FastAPI app" in summary

    def test_from_tool_calls_when_content_empty(self):
        messages = [{"role": "user", "content": ""}]
        tool_calls = [{"id": "1", "name": "terminal"}, {"id": "2", "name": "write_file"}]
        summary = generate_summary(messages, tool_calls)
        assert "terminal" in summary

    def test_empty_returns_placeholder(self):
        summary = generate_summary([], [])
        assert "(no content)" in summary


class TestCommitStore:
    def test_commit_and_get(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = CommitStore(db_path=Path(tmpdir) / "test.db")
            commit = store.commit(
                messages=[{"role": "user", "content": "test"}],
                tool_calls=[{"id": "1", "name": "terminal"}],
                tool_results=[{"id": "1", "result": "ok"}],
                reasoning="thinking",
                metadata={"model": "test"},
                agent_id="agent-1",
                task_id="task-1",
            )
            assert commit.id
            assert commit.task_id == "task-1"

            retrieved = store.get_commit(commit.id)
            assert retrieved is not None
            assert retrieved.id == commit.id
            assert retrieved.messages[0]["content"] == "test"

    def test_commit_id_is_content_hash(self):
        messages = [{"role": "user", "content": "test"}]
        commit_id = compute_commit_id(messages, [], [], None, {})
        with tempfile.TemporaryDirectory() as tmpdir:
            store = CommitStore(db_path=Path(tmpdir) / "test.db")
            commit = store.commit(
                messages=messages, tool_calls=[], tool_results=[],
                reasoning=None, metadata={}, agent_id="a", task_id="t",
            )
            assert commit.id == commit_id

    def test_log_returns_newest_first(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = CommitStore(db_path=Path(tmpdir) / "test.db")
            c1 = store.commit(
                messages=[{"role": "user", "content": "first"}],
                tool_calls=[], tool_results=[], reasoning=None, metadata={},
                agent_id="a", task_id="task-log",
            )
            c2 = store.commit(
                messages=[{"role": "user", "content": "second"}],
                tool_calls=[], tool_results=[], reasoning=None, metadata={},
                agent_id="a", task_id="task-log", parent_id=c1.id,
            )
            log = store.log("task-log")
            assert len(log) == 2
            assert log[0].id == c2.id
            assert log[1].id == c1.id

    def test_diff_shows_added_removed_tools(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = CommitStore(db_path=Path(tmpdir) / "test.db")
            c1 = store.commit(
                messages=[{"role": "user", "content": "step1"}],
                tool_calls=[{"id": "1", "name": "terminal"}],
                tool_results=[], reasoning=None, metadata={},
                agent_id="a", task_id="task-diff",
            )
            c2 = store.commit(
                messages=[{"role": "user", "content": "step2"}],
                tool_calls=[
                    {"id": "1", "name": "terminal"},
                    {"id": "2", "name": "write_file"},
                ],
                tool_results=[], reasoning=None, metadata={},
                agent_id="a", task_id="task-diff", parent_id=c1.id,
            )
            result = store.diff(c1.id, c2.id)
            assert "tool_calls" in result
            assert "added" in result["tool_calls"]
            assert len(result["tool_calls"]["added"]) == 1
            assert result["tool_calls"]["added"][0]["name"] == "write_file"

    def test_branch_creates_new_commit_with_parent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = CommitStore(db_path=Path(tmpdir) / "test.db")
            original = store.commit(
                messages=[{"role": "user", "content": "original"}],
                tool_calls=[], tool_results=[], reasoning=None, metadata={},
                agent_id="agent-a", task_id="task-branch",
            )
            branched = store.branch(original.id, "task-branch-2", "agent-b")
            assert branched is not None
            assert branched.parent_id == original.id
            assert branched.task_id == "task-branch-2"
            assert branched.agent_id == "agent-b"

    def test_reference_returns_full_context(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = CommitStore(db_path=Path(tmpdir) / "test.db")
            commit = store.commit(
                messages=[{"role": "user", "content": "remember this"}],
                tool_calls=[{"id": "1", "name": "read_file"}],
                tool_results=[{"id": "1", "result": "file contents"}],
                reasoning="useful reasoning",
                metadata={"model": "claude-sonnet-4"},
                agent_id="agent-x", task_id="task-ref",
            )
            ref = store.reference(commit.id)
            assert ref is not None
            assert len(ref["messages"]) == 1
            assert ref["messages"][0]["content"] == "remember this"
            assert ref["reasoning"] == "useful reasoning"
            assert ref["metadata"]["model"] == "claude-sonnet-4"

    def test_duplicate_commit_id_deduplicates(self):
        """Identical commits produce identical IDs (INSERT OR IGNORE)."""
        messages = [{"role": "user", "content": "same"}]
        with tempfile.TemporaryDirectory() as tmpdir:
            store = CommitStore(db_path=Path(tmpdir) / "test.db")
            c1 = store.commit(
                messages=messages, tool_calls=[], tool_results=[],
                reasoning=None, metadata={}, agent_id="a", task_id="t",
            )
            c2 = store.commit(
                messages=messages, tool_calls=[], tool_results=[],
                reasoning=None, metadata={}, agent_id="a", task_id="t",
            )
            # ID is same since content is same (deduplication)
            assert c1.id == c2.id


class TestAgentCommitTool:
    def test_commit_action(self):
        result = agent_commit_tool(
            action="commit",
            messages=[{"role": "user", "content": "build api"}],
            tool_calls=[{"id": "1", "name": "terminal"}],
            tool_results=[{"id": "1", "result": "done"}],
            reasoning="thinking",
            metadata={"model": "test"},
            agent_id="test-agent",
            task_id="tool-test-task",
        )
        d = json.loads(result)
        assert "id" in d
        assert d["parent_id"] is None

    def test_log_action_requires_task_id(self):
        result = agent_commit_tool(action="log", task_id="")
        d = json.loads(result)
        assert "error" in d
        assert "task_id required" in d["error"]

    def test_diff_requires_both_commit_ids(self):
        result = agent_commit_tool(action="diff", commit_a_id="abc", commit_b_id="")
        d = json.loads(result)
        assert "error" in d

    def test_branch_requires_commit_id_and_new_task_id(self):
        result = agent_commit_tool(action="branch", commit_id="", new_task_id="")
        d = json.loads(result)
        assert "error" in d

    def test_reference_nonexistent_returns_error(self):
        result = agent_commit_tool(action="reference", commit_id="notexist")
        d = json.loads(result)
        assert "error" in d

    def test_unknown_action_returns_error(self):
        result = agent_commit_tool(action="foobar")
        d = json.loads(result)
        assert "error" in d
        assert "unknown action" in d["error"]


class TestAgentCommitSchema:
    def test_schema_has_all_actions(self):
        action_enum = AGENT_COMMIT_SCHEMA["parameters"]["properties"]["action"]["enum"]
        for a in ["commit", "log", "diff", "branch", "reference", "get"]:
            assert a in action_enum

    def test_schema_is_valid_openai_format(self):
        assert AGENT_COMMIT_SCHEMA["name"] == "agent_commit"
        assert AGENT_COMMIT_SCHEMA["parameters"]["type"] == "object"
        assert "action" in AGENT_COMMIT_SCHEMA["parameters"]["required"]

    def test_commit_is_required_action(self):
        required = AGENT_COMMIT_SCHEMA["parameters"]["required"]
        assert "action" in required