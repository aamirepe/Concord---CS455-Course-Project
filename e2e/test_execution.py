"""Running code together: live status and output in every console, cancelling, metrics and viewers."""
from __future__ import annotations

import re
import urllib.error

import pytest
from conftest import APP, open_file, set_text, text_of
from playwright.sync_api import Locator, Page, expect

# Records every status each console block shows, in order, so a fast QUEUED or
# STARTING that has already moved on by the time we look is still seen.
RECORD_STATUSES = """() => {
    window.__statuses = {};
    const note = () => document.querySelectorAll('.run').forEach((run) => {
        const label = run.getAttribute('aria-label');
        const status = run.querySelector('.run-status')?.textContent;
        const seen = (window.__statuses[label] ||= []);
        if (status && seen[seen.length - 1] !== status) seen.push(status);
    });
    note();
    new MutationObserver(note).observe(document.body, { subtree: true, childList: true, characterData: true });
}"""

SLOW_PROGRAM = "import time\nprint('started', flush=True)\ntime.sleep(4)\nprint('total', 6 * 7)\n"
FOREVER = "print('looping', flush=True)\nwhile True:\n    pass\n"


def statuses_of_last_run(page: Page) -> list[str]:
    label = page.locator(".run").last.get_attribute("aria-label")
    return page.evaluate("label => window.__statuses[label]", label)


def run_statuses(page: Page) -> Locator:
    return page.locator(".run-status")


def test_a_run_shows_every_status_and_its_output_in_both_consoles(open_as, api):
    api.file("e2e_run_status.py")
    alice, bob = open_as("alice"), open_as("bob")
    open_file(alice, "e2e_run_status.py")
    open_file(bob, "e2e_run_status.py")
    set_text(alice, SLOW_PROGRAM)
    for page in (alice, bob):
        page.evaluate(RECORD_STATUSES)

    alice.get_by_role("button", name="Run", exact=True).click()

    for page in (alice, bob):
        expect(page.locator(".run").last).to_contain_text("total 42", timeout=90_000)
        expect(page.locator(".run").last).to_contain_text("by alice")
        expect(run_statuses(page).last).to_have_text("completed")
        seen = statuses_of_last_run(page)
        # QUEUED, STARTING and RUNNING appear in this order before the run completes.
        order = [s for s in seen if s in ("queued", "starting", "running", "completed")]
        assert order == ["queued", "starting", "running", "completed"], seen


def test_cancelling_an_infinite_loop_shows_cancelled_in_both_consoles(open_as, api):
    api.file("e2e_cancel.py")
    alice, bob = open_as("alice"), open_as("bob")
    open_file(alice, "e2e_cancel.py")
    open_file(bob, "e2e_cancel.py")
    set_text(alice, FOREVER)

    alice.get_by_role("button", name="Run", exact=True).click()
    for page in (alice, bob):
        expect(run_statuses(page).last).to_have_text("running", timeout=90_000)

    alice.get_by_role("button", name="Cancel", exact=True).click()
    for page in (alice, bob):
        expect(run_statuses(page).last).to_have_text("cancelled", timeout=60_000)
    # The run is over, so Run is available again.
    expect(alice.get_by_role("button", name="Run", exact=True)).to_be_enabled()


def test_the_metrics_page_loads_for_a_logged_in_member(open_as):
    bob = open_as("bob")
    bob.goto(f"{APP}/metrics")

    expect(bob).to_have_url(re.compile(r"/metrics$"))
    expect(bob.get_by_role("heading", name="Metrics", exact=True)).to_be_visible()
    expect(bob.get_by_role("heading", name="Execution summary")).to_be_visible()
    expect(bob.locator("pre")).to_contain_text("status_counts")
    expect(bob.get_by_role("heading", name="Recent events")).to_be_visible()


def test_a_viewer_cannot_edit_or_run(open_as, api):
    fid = api.file("e2e_viewer.py")
    pid = api.project_id()
    alice, carol = open_as("alice"), open_as("carol")
    open_file(alice, "e2e_viewer.py")
    set_text(alice, "print('only alice edits')\n")
    # Not open_file(): that waits for "All changes synced", which a viewer has no edits to reach.
    carol.locator(".file-node", has_text="e2e_viewer.py").first.click()
    expect(carol.locator(".editor-tab.active")).to_contain_text("e2e_viewer.py")
    carol.wait_for_function("() => window.monaco.editor.getEditors()[0].getModel().getValue().includes('only alice')")

    # Edits: the editor is read-only, so typing changes nothing, for her or for alice.
    expect(carol.get_by_role("note").filter(has_text="Read-only: viewers can't edit")).to_be_visible()
    carol.click(".view-lines")
    carol.keyboard.type("vandalised")
    assert text_of(carol) == "print('only alice edits')\n"
    assert text_of(alice) == "print('only alice edits')\n"

    # Run: the button is there, but the server refuses and no run appears.
    carol.get_by_role("button", name="Run", exact=True).click()
    expect(carol.locator(".toast-error")).to_contain_text("requires editor+ role to run code", timeout=20_000)
    expect(carol.locator(".run")).to_have_count(0)
    expect(alice.locator(".run")).to_have_count(0)

    with pytest.raises(urllib.error.HTTPError) as refused:
        api.call("POST", f"/projects/{pid}/executions", user="carol", body={"file_id": fid})
    assert refused.value.code == 403
