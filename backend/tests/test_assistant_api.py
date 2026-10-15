"""End-to-end tests for the AI assistant router, through the real HTTP API.

Before the Phase 4 fixes, 4 of these 5 endpoints referenced names that don't
exist anywhere in the codebase (_get_session_config, CollabSession,
publish_session_event) and read/wrote a session_id field that doesn't exist
on either model — every request beyond POST /config crashed immediately.
"""
from __future__ import annotations

import pytest

from tests.agent_fakes import ScriptedProvider, answer, call, set_file_text, tool_turn


@pytest.mark.asyncio
async def test_owner_can_configure_assistant(client, project):
    a = project["owner"]
    project_id = project["id"]
    h = {"Authorization": f"Bearer {a['token']}"}

    r = await client.post(
        f"/projects/{project_id}/assist/config",
        json={"provider": "mistral", "api_key": "sk-test-1234567890"},
        headers=h,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "mistral"
    assert body["project_id"] == project_id
    assert body["is_active"] is True


@pytest.mark.asyncio
async def test_configure_rejects_short_api_key(client, project):
    a = project["owner"]
    h = {"Authorization": f"Bearer {a['token']}"}

    r = await client.post(
        f"/projects/{project['id']}/assist/config",
        json={"provider": "mistral", "api_key": "short"},
        headers=h,
    )
    assert r.status_code == 422  # fails AIAssistantConfigCreate's min_length=10


@pytest.mark.asyncio
async def test_non_owner_cannot_configure_assistant(client, project):
    a, b = project["owner"], project["other"]
    project_id = project["id"]

    r = await client.post(
        f"/projects/{project_id}/members",
        json={"user_id": b["id"], "role": "editor"},
        headers={"Authorization": f"Bearer {a['token']}"},
    )
    assert r.status_code == 201

    r = await client.post(
        f"/projects/{project_id}/assist/config",
        json={"provider": "mistral", "api_key": "sk-test-1234567890"},
        headers={"Authorization": f"Bearer {b['token']}"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_get_and_update_config_round_trip(client, project):
    a = project["owner"]
    project_id = project["id"]
    h = {"Authorization": f"Bearer {a['token']}"}

    r = await client.post(
        f"/projects/{project_id}/assist/config",
        json={"provider": "mistral", "api_key": "sk-test-1234567890"},
        headers=h,
    )
    assert r.status_code == 200

    r = await client.get(f"/projects/{project_id}/assist/config", headers=h)
    assert r.status_code == 200
    assert r.json()["provider"] == "mistral"

    r = await client.patch(
        f"/projects/{project_id}/assist/config", json={"is_active": False}, headers=h
    )
    assert r.status_code == 200
    assert r.json()["is_active"] is False


def _use_provider(monkeypatch: pytest.MonkeyPatch, provider) -> None:
    import app.llm.service as service_module

    monkeypatch.setattr(service_module.llm_manager, "get_provider", lambda *a, **kw: provider)


async def _configure(client, project) -> dict:
    h = {"Authorization": f"Bearer {project['owner']['token']}"}
    r = await client.post(
        f"/projects/{project['id']}/assist/config",
        json={"provider": "mistral", "api_key": "sk-test-1234567890"},
        headers=h,
    )
    assert r.status_code == 200
    return h


@pytest.mark.asyncio
async def test_request_assistant_end_to_end(client, project, monkeypatch: pytest.MonkeyPatch):
    h = await _configure(client, project)
    project_id = project["id"]
    fake = ScriptedProvider(answer("```python\nprint('hi')\n```", tokens=7))
    _use_provider(monkeypatch, fake)

    r = await client.post(
        f"/projects/{project_id}/assist", json={"prompt": "write a hello world"}, headers=h
    )
    assert r.status_code == 200
    body = r.json()
    # Fences are kept so the frontend can render the explanation as markdown
    assert body["response"] == "```python\nprint('hi')\n```"
    assert body["proposal"] is None
    assert body["provider"] == "mistral"
    assert body["steps"] == []
    assert body["total_tokens"] == 14

    r = await client.get(f"/projects/{project_id}/assist/history", headers=h)
    assert r.status_code == 200
    history = r.json()
    assert len(history) == 1
    assert history[0]["project_id"] == project_id
    assert history[0]["is_successful"] is True
    assert history[0]["mode"] == "ask"
    assert history[0]["prompt_tokens"] == 7
    assert history[0]["total_tokens"] == 14


@pytest.mark.asyncio
async def test_fix_mode_reads_file_and_returns_proposal(
    client, db, project, monkeypatch: pytest.MonkeyPatch
):
    h = await _configure(client, project)
    file_id = project["file_id"]
    await set_file_text(db, file_id, "for i in range(n):\n    print(i)\n")
    fake = ScriptedProvider(
        tool_turn(call("get_last_execution", "c1", file_id=file_id)),
        tool_turn(call("read_file", "c2", file_id=file_id)),
        tool_turn(call(
            "propose_edit", "c3", file_id=file_id,
            original="range(n)", proposed="range(n + 1)", explanation="include n",
        )),
        answer("The loop stops one short; I proposed including n."),
    )
    _use_provider(monkeypatch, fake)

    r = await client.post(
        f"/projects/{project['id']}/assist",
        json={"prompt": "fix my loop", "mode": "fix", "file_id": file_id},
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["proposal"] == {
        "file_id": file_id,
        "original_text": "range(n)",
        "proposed_text": "range(n + 1)",
        "explanation": "include n",
        "syntax_ok": True,
    }
    assert [s["tool"] for s in body["steps"]] == ["get_last_execution", "read_file", "propose_edit"]
    assert all(s["ok"] for s in body["steps"])
    # The file content reached the model through read_file, not the prompt
    assert any("for i in range(n):" in t for t in fake.tool_results())
    # The user message names the active file so the model knows what "my loop" is
    user_msg = fake.requests[0]["messages"][-1]["content"]
    assert "main.py" in user_msg and "fix my loop" in user_msg
    # Proposing writes nothing
    r = await client.get(f"/projects/{project['id']}/assist/history", headers=h)
    assert r.json()[0]["file_id"] == file_id


@pytest.mark.asyncio
async def test_context_code_is_used_when_no_file_id(client, project, monkeypatch: pytest.MonkeyPatch):
    h = await _configure(client, project)
    fake = ScriptedProvider(answer("ok"))
    _use_provider(monkeypatch, fake)

    r = await client.post(
        f"/projects/{project['id']}/assist",
        json={"prompt": "what does this do", "context_code": "x = 42"},
        headers=h,
    )
    assert r.status_code == 200
    assert "x = 42" in fake.requests[0]["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_selection_reaches_the_model(client, project, monkeypatch: pytest.MonkeyPatch):
    h = await _configure(client, project)
    fake = ScriptedProvider(answer("ok"))
    _use_provider(monkeypatch, fake)
    body = {"prompt": "explain this", "file_id": project["file_id"],
            "selection": {"start_line": 3, "end_line": 4, "text": "total = sum(xs)"}}

    r = await client.post(f"/projects/{project['id']}/assist", json=body, headers=h)
    assert r.status_code == 200, r.text
    assert "[The user selected lines 3-4:]" in fake.requests[0]["messages"][-1]["content"]

    body["selection"] = {"start_line": 0, "end_line": 1, "text": "x"}
    assert (await client.post(f"/projects/{project['id']}/assist", json=body, headers=h)).status_code == 422


@pytest.mark.asyncio
async def test_history_is_sent_to_the_model(client, project, monkeypatch: pytest.MonkeyPatch):
    h = await _configure(client, project)
    fake = ScriptedProvider(answer("sure"))
    _use_provider(monkeypatch, fake)

    r = await client.post(
        f"/projects/{project['id']}/assist",
        json={
            "prompt": "and now in reverse?",
            "history": [
                {"role": "user", "text": "print 1 to 3"},
                {"role": "assistant", "text": "for i in range(1, 4): print(i)"},
            ],
        },
        headers=h,
    )
    assert r.status_code == 200
    roles = [m["role"] for m in fake.requests[0]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert fake.requests[0]["messages"][1]["content"] == "print 1 to 3"


@pytest.mark.asyncio
async def test_server_key_is_used_without_project_config(
    client, project, monkeypatch: pytest.MonkeyPatch
):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "mistral_api_key", "server-key-123456")
    seen: dict = {}

    import app.llm.service as service_module

    def _get_provider(name, api_key, model=None):
        seen.update(name=name, api_key=api_key, model=model)
        return ScriptedProvider(answer("hi"))

    monkeypatch.setattr(service_module.llm_manager, "get_provider", _get_provider)
    h = {"Authorization": f"Bearer {project['owner']['token']}"}

    r = await client.get(f"/projects/{project['id']}/assist/status", headers=h)
    assert r.status_code == 200
    assert r.json() == {
        "enabled": True,
        "configured": True,
        "source": "server",
        "provider": "mistral",
        "model": "codestral-latest",
    }

    r = await client.post(f"/projects/{project['id']}/assist", json={"prompt": "hi"}, headers=h)
    assert r.status_code == 200
    assert seen == {"name": "mistral", "api_key": "server-key-123456", "model": "codestral-latest"}


@pytest.mark.asyncio
async def test_status_reports_not_configured_and_project_source(client, project):
    h = {"Authorization": f"Bearer {project['owner']['token']}"}
    r = await client.get(f"/projects/{project['id']}/assist/status", headers=h)
    assert r.json()["configured"] is False
    assert r.json()["source"] is None

    await _configure(client, project)
    r = await client.get(f"/projects/{project['id']}/assist/status", headers=h)
    body = r.json()
    assert body["configured"] is True
    assert body["source"] == "project"
    assert "api_key" not in body and "encrypted_api_key" not in body


@pytest.mark.asyncio
async def test_provider_without_tools_gets_single_completion(
    client, db, project, monkeypatch: pytest.MonkeyPatch
):
    h = await _configure(client, project)
    await set_file_text(db, project["file_id"], "print('stored')")
    fake = ScriptedProvider(answer("It prints stored."), supports_tools=False)
    _use_provider(monkeypatch, fake)

    r = await client.post(
        f"/projects/{project['id']}/assist",
        json={"prompt": "explain", "mode": "explain", "file_id": project["file_id"]},
        headers=h,
    )
    assert r.status_code == 200
    assert r.json()["proposal"] is None
    assert fake.requests[0]["tools"] is None
    assert "print('stored')" in fake.requests[0]["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_file_from_another_project_is_rejected(client, project, monkeypatch: pytest.MonkeyPatch):
    h = await _configure(client, project)
    _use_provider(monkeypatch, ScriptedProvider())
    r = await client.post(
        f"/projects/{project['id']}/assist", json={"prompt": "hi", "file_id": 99999}, headers=h
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_provider_failure_is_logged_and_returns_502(client, project, monkeypatch: pytest.MonkeyPatch):
    h = await _configure(client, project)

    class _Broken(ScriptedProvider):
        async def chat(self, *a, **kw):
            raise RuntimeError("upstream down")

    _use_provider(monkeypatch, _Broken())
    r = await client.post(f"/projects/{project['id']}/assist", json={"prompt": "hi"}, headers=h)
    assert r.status_code == 502
    assert "upstream down" in r.json()["detail"]

    r = await client.get(f"/projects/{project['id']}/assist/history", headers=h)
    assert r.json()[0]["is_successful"] is False
    assert r.json()[0]["error_message"] == "upstream down"


@pytest.mark.asyncio
async def test_request_assistant_rate_limited(client, project, monkeypatch: pytest.MonkeyPatch):
    a = project["owner"]
    project_id = project["id"]
    h = {"Authorization": f"Bearer {a['token']}"}

    import app.api.assistant as assistant_module

    monkeypatch.setattr(
        assistant_module.llm_manager,
        "check_rate_limit",
        lambda *a, **kw: (False, {"limit": 10, "remaining": 0, "reset_time": 30}),
    )

    r = await client.post(
        f"/projects/{project_id}/assist", json={"prompt": "hi"}, headers=h
    )
    assert r.status_code == 429
    assert r.headers["Retry-After"] == "30"
    assert r.json()["detail"].endswith("Try again in 30 s.")


@pytest.mark.asyncio
async def test_non_members_do_not_use_up_the_projects_ai_quota(client, project, monkeypatch: pytest.MonkeyPatch):
    import app.api.assistant as assistant_module

    counted: list[int] = []
    monkeypatch.setattr(
        assistant_module.llm_manager,
        "check_rate_limit",
        lambda pid: (counted.append(pid), (True, {"limit": 10, "remaining": 9, "reset_time": 60}))[1],
    )
    h = {"Authorization": f"Bearer {project['other']['token']}"}
    r = await client.post(f"/projects/{project['id']}/assist", json={"prompt": "hi"}, headers=h)
    assert r.status_code == 403
    assert counted == []


@pytest.mark.asyncio
async def test_request_assistant_without_config_returns_400(client, project):
    a = project["owner"]
    h = {"Authorization": f"Bearer {a['token']}"}

    r = await client.post(
        f"/projects/{project['id']}/assist", json={"prompt": "hi"}, headers=h
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_non_member_cannot_use_assistant_endpoints(client, project):
    b = project["other"]
    project_id = project["id"]
    h = {"Authorization": f"Bearer {b['token']}"}

    r = await client.get(f"/projects/{project_id}/assist/config", headers=h)
    assert r.status_code == 403

    r = await client.post(f"/projects/{project_id}/assist", json={"prompt": "hi"}, headers=h)
    assert r.status_code == 403

    r = await client.get(f"/projects/{project_id}/assist/history", headers=h)
    assert r.status_code == 403




def _capture_broadcasts(monkeypatch: pytest.MonkeyPatch, project_id: int) -> list[dict]:
    import json

    from app.realtime.manager import room_manager

    sent: list[dict] = []

    def fake_broadcast(pid, payload, exclude=None):
        assert pid == project_id
        sent.append(json.loads(payload))

    monkeypatch.setattr(room_manager, "broadcast_to_project", fake_broadcast)
    return sent


@pytest.mark.asyncio
async def test_progress_events_are_broadcast_without_content(
    client, db, project, monkeypatch: pytest.MonkeyPatch
):
    h = await _configure(client, project)
    file_id = project["file_id"]
    await set_file_text(db, file_id, "secret_code = 1\n")
    fake = ScriptedProvider(
        tool_turn(call("read_file", "c1", file_id=file_id)),
        tool_turn(call("propose_edit", "c2", file_id=file_id,
                       original="secret_code = 1", proposed="secret_code = 2", explanation="bump")),
        answer("private answer"),
    )
    _use_provider(monkeypatch, fake)
    sent = _capture_broadcasts(monkeypatch, project["id"])

    r = await client.post(
        f"/projects/{project['id']}/assist",
        json={"prompt": "private prompt", "mode": "fix", "file_id": file_id, "request_id": "req-1"},
        headers=h,
    )
    assert r.status_code == 200, r.text

    assert [e["op"] for e in sent] == ["AI_STARTED", "AI_STEP", "AI_STEP", "AI_FINISHED"]
    assert {e["request_id"] for e in sent} == {"req-1"}
    assert {e["user_id"] for e in sent} == {project["owner"]["id"]}
    assert sent[0]["mode"] == "fix" and sent[0]["file_id"] == file_id and sent[0]["username"]
    assert [(e["tool"], e["ok"]) for e in sent[1:3]] == [("read_file", True), ("propose_edit", True)]
    assert sent[-1] == {
        "op": "AI_FINISHED", "request_id": "req-1", "user_id": project["owner"]["id"],
        "ok": True, "has_proposal": True, "file_id": file_id,
    }
    # Other members only learn *that* the AI ran, not what was asked or answered
    wire = str(sent)
    for private in ("private prompt", "private answer", "secret_code"):
        assert private not in wire


@pytest.mark.asyncio
async def test_failed_run_still_broadcasts_finished(client, project, monkeypatch: pytest.MonkeyPatch):
    h = await _configure(client, project)

    class _Broken(ScriptedProvider):
        async def chat(self, *a, **kw):
            raise RuntimeError("upstream down")

    _use_provider(monkeypatch, _Broken())
    sent = _capture_broadcasts(monkeypatch, project["id"])
    r = await client.post(f"/projects/{project['id']}/assist", json={"prompt": "hi"}, headers=h)
    assert r.status_code == 502
    assert [e["op"] for e in sent] == ["AI_STARTED", "AI_FINISHED"]
    assert sent[-1]["ok"] is False and sent[-1]["has_proposal"] is False
    # No request_id from the client: the server made one up, same for both events
    assert sent[0]["request_id"] and sent[0]["request_id"] == sent[1]["request_id"]


@pytest.mark.asyncio
async def test_request_id_must_be_a_simple_token(client, project, monkeypatch: pytest.MonkeyPatch):
    h = await _configure(client, project)
    _use_provider(monkeypatch, ScriptedProvider(answer("x")))
    r = await client.post(
        f"/projects/{project['id']}/assist", json={"prompt": "hi", "request_id": "<script>"}, headers=h
    )
    assert r.status_code == 422


def test_provider_column_stores_lowercase_values():
    """Postgres' aiprovider type only accepts 'mistral'/'huggingface'; storing
    the member names ('MISTRAL') made every AI request a 500 there."""
    from app.models import AIAssistantConfig, AIAssistantRequest

    for model in (AIAssistantConfig, AIAssistantRequest):
        assert list(model.__table__.c.provider.type.enums) == ["mistral", "huggingface"]
