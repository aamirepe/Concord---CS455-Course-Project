"""Searching a project's files, including edits nobody has saved yet."""
from __future__ import annotations

import re

import pytest

from app.api import search as search_module
from app.models import DocumentVersion
from app.realtime.crdt import doc_to_bytes, new_doc
from app.realtime.manager import room_manager


def _auth(user: dict) -> dict:
    return {"Authorization": f"Bearer {user['token']}"}


@pytest.mark.asyncio
async def test_search_finds_saved_and_live_text(client, db, project):
    pid, owner = project["id"], project["owner"]
    db.add(DocumentVersion(file_id=project["file_id"], version_number=1,
                           snapshot_bytes=doc_to_bytes(new_doc("def total(xs):\n    return Total(xs)\n"))))
    await db.commit()
    other = (await client.post(f"/projects/{pid}/files", json={"path": "util.py"}, headers=_auth(owner))).json()
    room = await room_manager.get_or_create_room(pid, other["id"])
    room.doc = new_doc("x = 1\ny = total  # unsaved\n")  # open in someone's editor, not saved

    r = await client.get(f"/projects/{pid}/search", params={"q": "total"}, headers=_auth(owner))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3 and not body["truncated"]
    by_path = {f["path"]: f["matches"] for f in body["files"]}
    assert [(m["line"], m["column"]) for m in by_path["main.py"]] == [(1, 5), (2, 12)]
    assert by_path["util.py"][0] == {"line": 2, "column": 5, "length": 5, "preview": "y = total  # unsaved",
                                    "preview_column": 4}

    r = await client.get(f"/projects/{pid}/search", params={"q": "Total", "case": "true"}, headers=_auth(owner))
    assert r.json()["total"] == 1
    r = await client.get(f"/projects/{pid}/search", params={"q": "tot", "word": "true"}, headers=_auth(owner))
    assert r.json()["total"] == 0


@pytest.mark.asyncio
async def test_search_is_literal_and_members_only(client, db, project):
    pid = project["id"]
    db.add(DocumentVersion(file_id=project["file_id"], version_number=1,
                           snapshot_bytes=doc_to_bytes(new_doc("a.*b\n(a+)+$\n"))))
    await db.commit()
    r = await client.get(f"/projects/{pid}/search", params={"q": "(a+)+$"}, headers=_auth(project["owner"]))
    assert r.json()["total"] == 1  # the characters themselves, not a regular expression
    assert (await client.get(f"/projects/{pid}/search", params={"q": "a"}, headers=_auth(project["other"]))).status_code == 403
    assert (await client.get(f"/projects/{pid}/search", params={"q": ""}, headers=_auth(project["owner"]))).status_code == 422


@pytest.mark.asyncio
async def test_search_caps_results_and_long_lines(client, db, project, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(search_module, "MAX_MATCHES", 3)
    long_line = "x" * 300 + "needle" + "y" * 300
    db.add(DocumentVersion(file_id=project["file_id"], version_number=1,
                           snapshot_bytes=doc_to_bytes(new_doc(f"{long_line}\nneedle\nneedle\nneedle\n"))))
    await db.commit()
    body = (await client.get(f"/projects/{project['id']}/search", params={"q": "needle"},
                             headers=_auth(project["owner"]))).json()
    assert body["total"] == 3 and body["truncated"]
    first = body["files"][0]["matches"][0]
    assert len(first["preview"]) == 200
    assert first["preview"][first["preview_column"]:first["preview_column"] + 6] == "needle"


def _units(text: str) -> bytes:
    return text.encode("utf-16-le")


def test_offsets_count_utf16_units_like_the_browser():
    pattern = re.compile(re.escape("needle"))
    (m,) = search_module.search_text("\U0001F600 needle", pattern, 10)
    assert (m.column, m.length, m.preview_column) == (4, 6, 3)  # the emoji is two units, not one

    # A match that itself contains a character outside the BMP.
    (m,) = search_module.search_text("ab\U0001F600cd", re.compile(re.escape("b\U0001F600c")), 10)
    assert (m.column, m.length) == (2, 4)


def test_preview_highlight_lines_up_after_non_bmp_characters():
    line = "\U0001F600" * 150 + "needle" + "y" * 150
    (m,) = search_module.search_text(line, re.compile("needle"), 10)
    units = _units(m.preview)
    assert units[2 * m.preview_column:2 * (m.preview_column + m.length)].decode("utf-16-le") == "needle"
