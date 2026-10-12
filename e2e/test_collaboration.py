"""Two people in one file: edits, presence, Save, history and restore."""
from __future__ import annotations

import uuid

from conftest import open_file, set_text, text_of
from playwright.sync_api import expect


def test_edits_and_presence_reach_the_other_person(open_as, api):
    api.file("e2e_collab.py")
    alice, bob = open_as("alice"), open_as("bob")
    open_file(alice, "e2e_collab.py")
    open_file(bob, "e2e_collab.py")

    tag = uuid.uuid4().hex[:6]
    set_text(alice, f"# {tag} from alice\nprint('hi')\n")
    bob.wait_for_function(f"() => window.monaco.editor.getEditors()[0].getModel().getValue().startsWith('# {tag}')")
    # bob sees alice in the file (tab and tree), and her named cursor
    expect(bob.locator(".editor-tab.active .presence-avatars")).to_have_attribute("title", "Here: alice")
    alice.click(".view-lines")
    bob.wait_for_function("() => (document.querySelector('[data-testid=cursor-styles]')?.textContent || '').includes('alice')")


def test_save_history_and_restore_reach_an_open_editor(open_as, api):
    api.file("e2e_history.py")
    alice, bob = open_as("alice"), open_as("bob")
    open_file(alice, "e2e_history.py")
    open_file(bob, "e2e_history.py")
    tag = uuid.uuid4().hex[:6]

    set_text(alice, f"# {tag} good\n")
    alice.get_by_role("button", name="Save", exact=True).click()
    expect(alice.locator(".sync-status[role=status]")).to_contain_text("Saved as version")
    set_text(alice, f"# {tag} broken (\n")

    alice.get_by_role("button", name="History", exact=True).click()
    alice.get_by_label("Saves only").check()
    expect(alice.locator(".version-list button").first).to_contain_text("Saved by alice")
    alice.once("dialog", lambda d: d.accept())
    alice.get_by_role("button", name="Restore this version").click()
    bob.wait_for_function(f"() => window.monaco.editor.getEditors()[0].getModel().getValue() === '# {tag} good\\n'")
    assert text_of(alice) == f"# {tag} good\n"
