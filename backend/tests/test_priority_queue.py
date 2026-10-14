"""FR-15: the next free execution slot goes to the highest-priority waiting
execution, not the one that arrived first.

``compute_priority`` and ``_select_highest_priority`` are pure functions (no
Redis), so the scoring itself is tested directly here, matching the project's
existing style of testing the queue by monkeypatching Redis at the call site
rather than running one in unit tests (see conftest.py's REDIS_URL comment).
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.core.config import Settings
from app.realtime.execution_queue import _select_highest_priority, compute_priority


@pytest.fixture
def settings() -> Settings:
    # Defaults from config.py, isolated from whatever a developer's .env sets.
    return Settings(_env_file=None)


def _payload(role: str, enqueued_at: float, *, debug: bool = False, language: str = "python") -> dict:
    return {"role": role, "debug": debug, "language": language, "enqueued_at": enqueued_at}


def test_higher_role_wins_at_equal_wait_time(settings: Settings):
    now = 1_000.0
    owner_score = compute_priority(role="owner", debug=False, language="python", enqueued_at=now, now=now, settings=settings)
    viewer_score = compute_priority(role="viewer", debug=False, language="python", enqueued_at=now, now=now, settings=settings)
    assert owner_score > viewer_score


def test_wait_time_ageing_always_overtakes_even_the_highest_possible_role_score(settings: Settings):
    """D1 NFR-D5: ageing must guarantee no request waits indefinitely
    *regardless of role* — so a fully-aged low-role run has to beat not just a
    fresh owner, but the worst case of all: a fresh debug run from an owner.
    A merely-tied score isn't enough, since a steady stream of fresh high-role
    arrivals could then starve the aged one forever on tie-breaking."""
    now = 1_000.0
    fresh_owner_debugging = compute_priority(
        role="owner", debug=True, language="python", enqueued_at=now, now=now, settings=settings,
    )
    aged_viewer = compute_priority(
        role="viewer", debug=False, language="python",
        enqueued_at=now - settings.priority_wait_saturation_seconds, now=now, settings=settings,
    )
    assert aged_viewer > fresh_owner_debugging


def test_wait_term_saturates_and_never_goes_negative(settings: Settings):
    now = 1_000.0
    at_saturation = compute_priority(
        role="viewer", debug=False, language="python",
        enqueued_at=now - settings.priority_wait_saturation_seconds, now=now, settings=settings,
    )
    way_past_saturation = compute_priority(
        role="viewer", debug=False, language="python",
        enqueued_at=now - settings.priority_wait_saturation_seconds * 10, now=now, settings=settings,
    )
    assert at_saturation == way_past_saturation
    # A clock skew that makes enqueued_at look like it's in the future must not
    # produce a negative wait term that would make waiting *worse*.
    future_enqueue = compute_priority(role="viewer", debug=False, language="python", enqueued_at=now + 5, now=now, settings=settings)
    just_enqueued = compute_priority(role="viewer", debug=False, language="python", enqueued_at=now, now=now, settings=settings)
    assert future_enqueue == just_enqueued


def test_debug_run_gets_a_bonus_over_a_plain_run_of_the_same_role(settings: Settings):
    now = 1_000.0
    plain = compute_priority(role="debugger", debug=False, language="python", enqueued_at=now, now=now, settings=settings)
    debugging = compute_priority(role="debugger", debug=True, language="python", enqueued_at=now, now=now, settings=settings)
    assert debugging > plain


def test_cpp_is_penalised_relative_to_an_otherwise_identical_run(settings: Settings):
    now = 1_000.0
    python_run = compute_priority(role="editor", debug=False, language="python", enqueued_at=now, now=now, settings=settings)
    cpp_run = compute_priority(role="editor", debug=False, language="cpp", enqueued_at=now, now=now, settings=settings)
    assert cpp_run < python_run


def test_select_highest_priority_returns_none_for_an_empty_queue(settings: Settings):
    assert _select_highest_priority({}, now=1_000.0, settings=settings) is None


def test_select_highest_priority_picks_the_top_scorer_not_arrival_order(settings: Settings):
    now = 1_000.0
    waiting = {
        # Arrived first, but a viewer with no wait yet.
        "1": _payload("viewer", enqueued_at=now),
        # Arrived later, but an owner — should win despite being newer.
        "2": _payload("owner", enqueued_at=now),
        # Arrived first of all, but it's C++ (penalised) and still a viewer.
        "3": _payload("viewer", enqueued_at=now - 1, language="cpp"),
    }
    assert _select_highest_priority(waiting, now=now, settings=settings) == "2"


def test_unknown_role_does_not_crash_and_ranks_at_the_bottom(settings: Settings):
    now = 1_000.0
    unknown = compute_priority(role="not-a-real-role", debug=False, language="python", enqueued_at=now, now=now, settings=settings)
    viewer = compute_priority(role="viewer", debug=False, language="python", enqueued_at=now, now=now, settings=settings)
    assert unknown == viewer


@pytest.mark.asyncio
async def test_cancelling_a_queued_run_drops_it_from_the_queue_not_just_the_db(
    client: AsyncClient, project: dict, no_op_enqueue: list[dict], monkeypatch: pytest.MonkeyPatch,
):
    """A still-waiting execution should never cost a slot once cancelled — it
    must come out of the priority queue immediately, not wait to be picked up
    by a worker and discovered there."""
    import app.realtime.execution_queue as execution_queue_module

    removed: list[int] = []
    signalled: list[int] = []

    async def _fake_remove(execution_id: int) -> bool:
        removed.append(execution_id)
        return True

    async def _fake_signal(execution_id: int) -> None:
        signalled.append(execution_id)

    monkeypatch.setattr(execution_queue_module, "remove_from_queue", _fake_remove)
    monkeypatch.setattr(execution_queue_module, "signal_cancel", _fake_signal)

    owner = project["owner"]
    headers = {"Authorization": f"Bearer {owner['token']}"}
    run = await client.post(
        f"/projects/{project['id']}/executions",
        json={"file_id": project["file_id"]},
        headers=headers,
    )
    assert run.status_code == 202, run.text
    execution_id = run.json()["id"]

    cancel = await client.post(
        f"/projects/{project['id']}/executions/{execution_id}/cancel", headers=headers,
    )
    assert cancel.status_code == 200, cancel.text
    assert cancel.json()["status"] == "cancelled"
    assert removed == [execution_id]
    assert signalled == [execution_id]
