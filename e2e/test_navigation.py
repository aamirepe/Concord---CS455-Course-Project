"""Tabs, Ctrl+P and project search."""
from __future__ import annotations

import uuid

from conftest import open_file, set_text
from playwright.sync_api import expect


def test_tabs_quick_open_and_search(open_as, api):
    api.file("e2e_nav_a.py")
    api.file("e2e_nav_b.py")
    alice = open_as("alice")
    open_file(alice, "e2e_nav_a.py")
    needle = f"needle_{uuid.uuid4().hex[:6]}"
    set_text(alice, f"x = 1\ny = 2\n{needle} = 3\n")
    open_file(alice, "e2e_nav_b.py")
    expect(alice.locator(".editor-tab")).to_contain_text(["e2e_nav_a.py", "e2e_nav_b.py"])

    # Ctrl+P back to the first file
    alice.click(".view-lines")
    alice.keyboard.press("Control+p")
    alice.get_by_label("File name").fill("nav_a")
    alice.keyboard.press("Enter")
    expect(alice.locator(".editor-tab.active")).to_contain_text("e2e_nav_a.py")

    # search from the other file, jump to the match
    alice.locator(".editor-tab", has_text="e2e_nav_b.py").click()
    alice.keyboard.press("Control+Shift+F")
    alice.get_by_label("Search all files").fill(needle)
    hit = alice.locator(".search-hit").first
    expect(hit).to_contain_text(needle)
    hit.click()
    expect(alice.locator(".editor-tab.active")).to_contain_text("e2e_nav_a.py")
    alice.wait_for_function(
        "n => { const e = window.monaco.editor.getEditors()[0]; const s = e.getSelection();"
        " return s && s.startLineNumber === 3 && e.getModel().getValueInRange(s) === n; }", arg=needle)

    # tabs survive a reload; closing one works
    alice.reload()
    expect(alice.locator(".editor-tab")).to_contain_text(["e2e_nav_a.py", "e2e_nav_b.py"])
    alice.get_by_label("Close e2e_nav_b.py").click(force=True)
    expect(alice.locator(".editor-tab", has_text="e2e_nav_b.py")).to_have_count(0)


def test_run_with_input_shows_in_everyones_console(open_as, api):
    api.file("e2e_input.py")
    bob, alice = open_as("bob"), open_as("alice")
    open_file(bob, "e2e_input.py")
    set_text(bob, "a = int(input())\nb = int(input())\nprint('total', a + b)\n")
    bob.get_by_role("button", name="Input", exact=True).click()
    bob.get_by_label("Program input").fill("20\n22\n")
    bob.get_by_role("button", name="Run", exact=True).click()
    expect(bob.locator(".run").last).to_contain_text("total 42", timeout=90_000)
    expect(bob.locator(".run").last).to_contain_text("by bob")
    expect(alice.locator(".run", has_text="total 42").last).to_contain_text("by bob")
