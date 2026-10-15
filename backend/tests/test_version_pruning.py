"""Thinning out old autosaves while keeping a usable history."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import DocumentVersion
from app.realtime import checkpoint
from app.realtime.checkpoint import VersionRow, prune_versions, versions_to_prune
from app.realtime.crdt import doc_to_bytes, new_doc

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def _rows(*ages_and_saves: tuple[timedelta, int | None]) -> list[VersionRow]:
    """Versions numbered oldest first from (age, saved_by) pairs."""
    return [VersionRow(id=n, version_number=n, created_at=NOW - age, saved_by=saved)
            for n, (age, saved) in enumerate(ages_and_saves, start=1)]


def test_recent_versions_saves_and_the_latest_are_always_kept():
    rows = _rows(
        (timedelta(days=3), 7),            # an old Save
        (timedelta(days=3, minutes=-1), None),
        (timedelta(minutes=50), None),     # last hour: kept
        (timedelta(minutes=10), None),
    )
    assert versions_to_prune(rows, NOW) == []  # the day-old autosave is alone in its day


def test_autosaves_are_thinned_to_one_per_ten_minutes_then_one_per_day():
    base = NOW.replace(minute=0) - timedelta(hours=5)  # 07:00, a 10-minute slot boundary
    rows = [
        VersionRow(1, 1, base + timedelta(minutes=1), None),
        VersionRow(2, 2, base + timedelta(minutes=4), None),
        VersionRow(3, 3, base + timedelta(minutes=8), None),   # newest of the 07:00 slot
        VersionRow(4, 4, base + timedelta(minutes=12), None),  # alone in 07:10
        VersionRow(5, 5, NOW - timedelta(days=2, hours=1), None),
        VersionRow(6, 6, NOW - timedelta(days=2), None),       # same day as 5, newer
        VersionRow(7, 7, NOW - timedelta(minutes=1), None),    # latest
    ]
    assert sorted(versions_to_prune(rows, NOW)) == [1, 2, 5]


def test_the_latest_version_is_kept_even_when_old():
    rows = _rows((timedelta(days=9), None), (timedelta(days=9, minutes=-5), None))
    assert versions_to_prune(rows, NOW) == [1]
    assert versions_to_prune([], NOW) == []


def test_naive_timestamps_from_sqlite_are_treated_as_utc():
    rows = [VersionRow(1, 1, (NOW - timedelta(days=2)).replace(tzinfo=None), None),
            VersionRow(2, 2, (NOW - timedelta(days=2, minutes=-1)).replace(tzinfo=None), None),
            VersionRow(3, 3, NOW.replace(tzinfo=None), None)]
    assert versions_to_prune(rows, NOW) == [1]


@pytest.mark.asyncio
async def test_prune_versions_deletes_only_busy_files_old_autosaves(db, project, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(checkpoint, "PRUNE_MIN_VERSIONS", 5)
    fid = project["file_id"]
    old = NOW - timedelta(days=3)
    for n in range(1, 9):  # 8 autosaves a minute apart, three days ago
        db.add(DocumentVersion(file_id=fid, version_number=n, snapshot_bytes=doc_to_bytes(new_doc(f"v{n}\n")),
                               created_at=old + timedelta(minutes=n), saved_by=project["owner"]["id"] if n == 2 else None))
    db.add(DocumentVersion(file_id=fid, version_number=9, snapshot_bytes=doc_to_bytes(new_doc("now\n")), created_at=NOW))
    await db.commit()

    assert await prune_versions(now=NOW) == 6
    kept = (await db.execute(select(DocumentVersion.version_number)
                             .where(DocumentVersion.file_id == fid)
                             .order_by(DocumentVersion.version_number))).scalars().all()
    assert kept == [2, 8, 9]  # the Save, the newest of that day, the latest
    assert await prune_versions(now=NOW) == 0
