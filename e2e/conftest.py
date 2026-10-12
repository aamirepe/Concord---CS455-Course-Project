"""Fixtures: log in as pilot users in separate browser contexts, plus a small API client."""
from __future__ import annotations

import json
import os
import re
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect, sync_playwright

APP = os.environ.get("CONCORD_APP", "http://localhost:5173")
API = os.environ.get("CONCORD_API", "http://localhost:8000")
PASSWORD = "pilot-pass-123"  # noqa: S105 -- the local pilot's demo accounts
PROJECT = "Pilot demo"
OUT = Path(os.environ.get("E2E_OUT", Path(__file__).parent / "out"))

expect.set_options(timeout=20_000)


class Api:
    def __init__(self) -> None:
        self.tokens: dict[str, str] = {}

    def call(self, method: str, path: str, user: str = "alice", body: object = None) -> object:
        if user not in self.tokens:
            self.tokens[user] = self._post("/auth/login", {"username": user, "password": PASSWORD})["access_token"]
        req = urllib.request.Request(
            API + path, method=method, data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.tokens[user]}"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 -- local test stack
            raw = r.read()
        return json.loads(raw) if raw else None

    def _post(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(API + path, method="POST", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
            return json.loads(r.read())

    def project_id(self) -> int:
        return next(p["id"] for p in self.call("GET", "/projects") if p["name"] == PROJECT)

    def file(self, path: str) -> int:
        """The id of ``path`` in the demo project, creating it if needed."""
        pid = self.project_id()
        for f in self.call("GET", f"/projects/{pid}/files"):
            if f["path"] == path:
                return f["id"]
        return self.call("POST", f"/projects/{pid}/files", body={"path": path})["id"]


@pytest.fixture(scope="session")
def api() -> Api:
    return Api()


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def open_as(browser: Browser, api: Api, request: pytest.FixtureRequest) -> Iterator[Callable[[str], Page]]:
    """``open_as("bob")``: a browser logged in as bob with the demo project open."""
    pages: dict[str, Page] = {}
    contexts = []

    def _open(user: str) -> Page:
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        contexts.append(ctx)
        page = ctx.new_page()
        page.goto(f"{APP}/login")
        page.fill("input[placeholder=username]", user)
        page.fill("input[placeholder=password]", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_url(re.compile(r"/$"))
        page.goto(f"{APP}/projects/{api.project_id()}")
        page.wait_for_selector(".monaco-editor", timeout=90_000)  # Monaco loads from a CDN
        pages[user] = page
        return page

    yield _open
    OUT.mkdir(parents=True, exist_ok=True)
    for user, page in pages.items():
        page.screenshot(path=str(OUT / f"{request.node.name}-{user}.png"))
    for ctx in contexts:
        ctx.close()


def open_file(page: Page, path: str) -> None:
    page.locator(".file-node", has_text=path).first.click()
    expect(page.locator(".editor-tab.active")).to_contain_text(path.split("/")[-1])
    expect(page.locator(".sync-status[role=status]")).to_contain_text("All changes synced")


def set_text(page: Page, text: str) -> None:
    """Replace the open file's text, like a paste (no auto-indent)."""
    page.evaluate("""t => { const m = window.monaco.editor.getEditors()[0].getModel();
        m.pushEditOperations([], [{ range: m.getFullModelRange(), text: t }], () => null); }""", text)
    expect(page.locator(".sync-status[role=status]")).to_contain_text("All changes synced")


def text_of(page: Page) -> str:
    return page.evaluate("() => window.monaco.editor.getEditors()[0].getModel().getValue()")


def click_margin(page: Page, line: int) -> None:
    """Click left of a line number (the breakpoint margin)."""
    box = page.locator(".monaco-editor .margin").first.bounding_box()
    top = page.evaluate(f"() => {{ const e = window.monaco.editor.getEditors()[0]; return e.getTopForLineNumber({line}) - e.getScrollTop(); }}")
    page.mouse.click(box["x"] + 8, box["y"] + top + 9)
