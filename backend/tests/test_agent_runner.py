"""Unit tests for the agent loop and its tools, with a scripted model."""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.agent.runner import TRUNCATED_ANSWER, run_agent
from app.agent.tools import AgentContext, run_tool, tool_specs
from app.models import DocumentVersion, Execution, ExecutionStatus, File, Project
from tests.agent_fakes import ScriptedProvider, answer, call, set_file_text, tool_turn


def _ctx(db, project, **kw) -> AgentContext:
    return AgentContext(
        db=db,
        project_id=project["id"],
        active_file_id=kw.pop("active_file_id", project["file_id"]),
        max_file_chars=kw.pop("max_file_chars", 20000),
        **kw,
    )


@pytest.mark.asyncio
async def test_answer_without_tools_ends_loop(db, project):
    fake = ScriptedProvider(answer("hello", tokens=5))
    result = await run_agent(_ctx(db, project), fake, "hi")
    assert result.answer == "hello"
    assert result.steps == []
    assert result.prompt_tokens == 5 and result.completion_tokens == 5
    assert result.truncated is False


@pytest.mark.asyncio
async def test_read_file_result_is_fed_back_and_usage_summed(db, project):
    await set_file_text(db, project["file_id"], "print('hi')")
    fake = ScriptedProvider(
        tool_turn(call("read_file", file_id=project["file_id"]), tokens=3),
        answer("It prints hi.", tokens=4),
    )
    result = await run_agent(_ctx(db, project), fake, "what does it do?", mode="explain")

    assert result.answer == "It prints hi."
    assert [(s.tool, s.ok) for s in result.steps] == [("read_file", True)]
    assert result.prompt_tokens == 7 and result.completion_tokens == 7
    second = fake.requests[1]["messages"]
    assert second[-2]["role"] == "assistant" and second[-2]["tool_calls"][0]["function"]["name"] == "read_file"
    assert second[-1]["role"] == "tool" and "print('hi')" in second[-1]["content"]
    assert second[-1]["tool_call_id"] == "c1"


@pytest.mark.asyncio
async def test_read_file_prefers_live_room(db, project):
    from app.realtime.crdt import new_doc
    from app.realtime.manager import room_manager

    await set_file_text(db, project["file_id"], "stale = 1")
    room = await room_manager.get_or_create_room(project["id"], project["file_id"])
    room.doc = new_doc("live = 2")
    try:
        out, ok = await run_tool(_ctx(db, project), "ask", "read_file", json.dumps({"file_id": project["file_id"]}))
    finally:
        room_manager._rooms.pop((project["id"], project["file_id"]), None)
    assert ok and "live = 2" in out and "stale" not in out


@pytest.mark.asyncio
async def test_read_file_truncates_large_files(db, project):
    await set_file_text(db, project["file_id"], "x" * 500)
    out, ok = await run_tool(
        _ctx(db, project, max_file_chars=100), "ask", "read_file", json.dumps({"file_id": project["file_id"]})
    )
    assert ok and out.endswith("[truncated]")
    assert out.count("x") == 100


@pytest.mark.asyncio
async def test_file_from_other_project_is_an_error_not_a_crash(db, project, two_users):
    other = Project(name="other", owner_id=two_users[1]["id"])
    db.add(other)
    await db.flush()
    foreign = File(project_id=other.id, path="secret.py", language="python")
    db.add(foreign)
    await db.commit()

    out, ok = await run_tool(_ctx(db, project), "ask", "read_file", json.dumps({"file_id": foreign.id}))
    assert not ok
    assert "no file with id" in out


@pytest.mark.asyncio
async def test_list_files_marks_active(db, project):
    out, ok = await run_tool(_ctx(db, project), "ask", "list_files", "{}")
    files = json.loads(out)
    assert ok and files == [
        {"id": project["file_id"], "path": "main.py", "language": "python", "active": True}
    ]


@pytest.mark.asyncio
async def test_get_last_execution_returns_latest_run(db, project):
    ctx = _ctx(db, project)
    out, _ = await run_tool(ctx, "fix", "get_last_execution", "{}")
    assert "has not been run" in out

    now = datetime.now(UTC)
    db.add_all([
        Execution(project_id=project["id"], file_id=project["file_id"], status=ExecutionStatus.COMPLETED,
                  queued_at=now.replace(year=2020), stdout="old", stderr=""),
        Execution(project_id=project["id"], file_id=project["file_id"], status=ExecutionStatus.FAILED,
                  queued_at=now, exit_code=1, stdout="", stderr="NameError: name 'x' is not defined"),
    ])
    await db.commit()
    out, ok = await run_tool(ctx, "fix", "get_last_execution", "{}")
    data = json.loads(out)
    assert ok
    assert data["status"] == "failed" and data["exit_code"] == 1
    assert "NameError" in data["stderr_tail"]


@pytest.mark.asyncio
async def test_propose_edit_records_proposal_and_writes_nothing(db, project):
    await set_file_text(db, project["file_id"], "total = 0\nfor i in range(n):\n    total += i\n")
    ctx = _ctx(db, project)
    fake = ScriptedProvider(
        tool_turn(call("propose_edit", file_id=project["file_id"], original="range(n)",
                       proposed="range(n + 1)", explanation="off by one")),
        answer("Proposed including n."),
    )
    result = await run_agent(ctx, fake, "fix it", mode="fix")

    assert result.proposal is not None
    assert result.proposal.proposed_text == "range(n + 1)"
    assert result.proposal.syntax_ok is True
    # Still exactly one stored version with the original text
    from sqlalchemy import select
    versions = (await db.execute(select(DocumentVersion))).scalars().all()
    assert len(versions) == 1


@pytest.mark.asyncio
async def test_propose_edit_with_missing_original_lets_model_retry(db, project):
    await set_file_text(db, project["file_id"], "def f():\n    return 1\n")
    fid = project["file_id"]
    fake = ScriptedProvider(
        tool_turn(call("propose_edit", "c1", file_id=fid, original="return  1", proposed="return 2", explanation="")),
        tool_turn(call("propose_edit", "c2", file_id=fid, original="return 1", proposed="return 2", explanation="")),
        answer("done"),
    )
    result = await run_agent(_ctx(db, project), fake, "make it return 2", mode="generate")

    assert [s.ok for s in result.steps] == [False, True]
    assert "not found verbatim" in fake.tool_results()[0]
    assert result.proposal is not None and result.proposal.original_text == "return 1"


@pytest.mark.asyncio
async def test_propose_edit_rejects_ambiguous_original(db, project):
    await set_file_text(db, project["file_id"], "x = 1\nx = 1\n")
    out, ok = await run_tool(
        _ctx(db, project), "fix", "propose_edit",
        json.dumps({"file_id": project["file_id"], "original": "x = 1", "proposed": "x = 2", "explanation": ""}),
    )
    assert not ok and "appears 2 times" in out


@pytest.mark.asyncio
async def test_propose_edit_flags_broken_syntax(db, project):
    await set_file_text(db, project["file_id"], "print(1)\n")
    ctx = _ctx(db, project)
    out, ok = await run_tool(
        ctx, "fix", "propose_edit",
        json.dumps({"file_id": project["file_id"], "original": "print(1)", "proposed": "print(1", "explanation": ""}),
    )
    assert ok and "would not parse" in out
    assert ctx.proposal is not None and ctx.proposal.syntax_ok is False


@pytest.mark.asyncio
async def test_read_only_modes_cannot_propose(db, project):
    await set_file_text(db, project["file_id"], "a = 1")
    names = {t["function"]["name"] for t in tool_specs("review")}
    assert "propose_edit" not in names

    out, ok = await run_tool(
        _ctx(db, project), "review", "propose_edit",
        json.dumps({"file_id": project["file_id"], "original": "a = 1", "proposed": "a = 2", "explanation": ""}),
    )
    assert not ok and "not available in review mode" in out


@pytest.mark.asyncio
async def test_bad_tool_input_is_reported_to_model(db, project):
    ctx = _ctx(db, project)
    assert (await run_tool(ctx, "ask", "read_file", "not json"))[1] is False
    assert (await run_tool(ctx, "ask", "read_file", json.dumps({"file_id": "abc"})))[1] is False
    out, ok = await run_tool(ctx, "ask", "read_file", json.dumps({"path": "main.py"}))
    assert not ok and "bad arguments" in out
    out, ok = await run_tool(ctx, "ask", "delete_everything", "{}")
    assert not ok


@pytest.mark.asyncio
async def test_check_syntax_tool(db, project):
    ctx = _ctx(db, project)
    assert (await run_tool(ctx, "ask", "check_syntax", json.dumps({"code": "x = ("})))[0].startswith("SyntaxError")
    assert (await run_tool(ctx, "ask", "check_syntax", json.dumps({"code": "x = 1"})))[0].startswith("OK")
    out, _ = await run_tool(ctx, "ask", "check_syntax", json.dumps({"code": "int main(){}", "language": "cpp"}))
    assert "only available for python" in out


@pytest.mark.asyncio
async def test_step_budget_truncates(db, project):
    fake = ScriptedProvider(*[tool_turn(call("list_files", f"c{i}")) for i in range(3)])
    result = await run_agent(_ctx(db, project), fake, "loop forever", max_steps=3)
    assert result.truncated is True
    assert result.answer == TRUNCATED_ANSWER
    assert len(result.steps) == 3


@pytest.mark.asyncio
async def test_mode_changes_system_prompt_and_tools(db, project):
    fake = ScriptedProvider(answer("a"), answer("b"))
    await run_agent(_ctx(db, project), fake, "q", mode="review")
    await run_agent(_ctx(db, project), fake, "q", mode="fix")
    review, fix = fake.requests
    assert "Review the active file" in review["messages"][0]["content"]
    assert "get_last_execution" in fix["messages"][0]["content"]
    assert len(review["tools"]) < len(fix["tools"])


@pytest.mark.asyncio
async def test_on_step_reports_each_tool_call_in_order(db, project):
    fid = project["file_id"]
    await set_file_text(db, fid, "a = 1")
    fake = ScriptedProvider(
        tool_turn(call("list_files", "c1"), call("read_file", "c2", file_id=fid)),
        tool_turn(call("read_file", "c3", file_id=424242)),
        answer("done"),
    )
    seen = []

    async def on_step(step):
        seen.append((step.tool, step.ok))

    result = await run_agent(_ctx(db, project), fake, "look", on_step=on_step)
    assert seen == [("list_files", True), ("read_file", True), ("read_file", False)]
    assert [(s.tool, s.ok) for s in result.steps] == seen


@pytest.mark.asyncio
async def test_failing_on_step_does_not_break_the_run(db, project):
    fake = ScriptedProvider(tool_turn(call("list_files")), answer("fine"))

    async def on_step(step):
        raise RuntimeError("socket gone")

    result = await run_agent(_ctx(db, project), fake, "look", on_step=on_step)
    assert result.answer == "fine" and len(result.steps) == 1


# --- context preloaded into the first message -----------------------------------

async def _first_user_message(db, project, prompt="q", **kw) -> str:
    fake = ScriptedProvider(answer("ok"))
    await run_agent(_ctx(db, project), fake, prompt, **kw)
    return fake.requests[0]["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_first_message_carries_the_active_file(db, project):
    await set_file_text(db, project["file_id"], "def f():\n    return 1\n")
    msg = await _first_user_message(db, project, "what is f?")
    assert "[Active file: main.py" in msg and "2 lines" in msg
    assert "```python\ndef f():\n    return 1\n\n```" in msg
    assert msg.endswith("what is f?")


@pytest.mark.asyncio
async def test_long_files_are_cut_off_with_a_pointer_to_read_file(db, project):
    from app.agent.runner import PRELOAD_CHARS

    await set_file_text(db, project["file_id"], "x" * (PRELOAD_CHARS + 50))
    msg = await _first_user_message(db, project)
    assert "cut off" in msg and "read_file" in msg
    assert "x" * (PRELOAD_CHARS + 1) not in msg


@pytest.mark.asyncio
async def test_backticks_in_the_file_cannot_close_the_fence(db, project):
    await set_file_text(db, project["file_id"], 's = "```"\n')
    msg = await _first_user_message(db, project)
    assert "````python\n" in msg


@pytest.mark.asyncio
async def test_fix_mode_includes_the_last_run(db, project):
    msg = await _first_user_message(db, project, mode="fix")
    assert "has not been run yet" in msg

    db.add(Execution(project_id=project["id"], file_id=project["file_id"], status=ExecutionStatus.FAILED,
                     exit_code=1, stdout="", stderr="NameError: name 'x' is not defined",
                     queued_at=datetime.now(UTC)))
    await db.commit()
    msg = await _first_user_message(db, project, mode="fix")
    assert "[Last run of this file:]" in msg and "NameError" in msg
    assert "Last run" not in await _first_user_message(db, project, mode="explain")


@pytest.mark.asyncio
async def test_the_selection_is_included(db, project):
    from app.agent.runner import Selection

    await set_file_text(db, project["file_id"], "a = 1\nb = 2\n")
    msg = await _first_user_message(db, project, selection=Selection(start_line=2, end_line=2, text="b = 2"))
    assert "[The user selected lines 2-2:]\n```python\nb = 2\n```" in msg


@pytest.mark.asyncio
async def test_no_active_file_means_just_the_prompt(db, project):
    fake = ScriptedProvider(answer("ok"))
    await run_agent(_ctx(db, project, active_file_id=None), fake, "hello")
    assert fake.requests[0]["messages"][-1]["content"] == "hello"
