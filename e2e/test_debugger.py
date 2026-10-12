"""Debugging together: dave sets a breakpoint and steps, bob watches."""
from __future__ import annotations

import re
import time

from conftest import click_margin, open_file, set_text
from playwright.sync_api import expect

PROGRAM = (
    "def add(a, b):\n    total = a + b\n    return total\n\n"
    "nums = [1, 2, 3]\ns = 0\nfor x in nums:\n    s = add(s, x)\nprint('sum', s)\n"
)


def test_debug_together(open_as, api):
    fid = api.file("e2e_debug.py")
    pid = api.project_id()
    api.call("PUT", f"/projects/{pid}/files/{fid}/breakpoints", body={"lines": []})
    # A session left over from an earlier (failed) run would block this one.
    active = api.call("GET", f"/projects/{pid}/debug")["active"]
    if active:
        api.call("POST", f"/projects/{pid}/debug/{active['execution_id']}/command", body={"command": "stop"})
        for _ in range(30):
            if not api.call("GET", f"/projects/{pid}/debug")["active"]:
                break
            time.sleep(1)
    dave, bob = open_as("dave"), open_as("bob")
    open_file(dave, "e2e_debug.py")
    set_text(dave, PROGRAM)
    open_file(bob, "e2e_debug.py")

    click_margin(dave, 3)
    expect(dave.locator(".bp-glyph")).to_have_count(1)
    expect(bob.locator(".bp-glyph")).to_have_count(1)
    bob.get_by_role("tab", name=re.compile(r"^Debug")).click()

    dave.get_by_role("button", name="Debug", exact=True).click()
    status = bob.locator(".debug-status")
    expect(status).to_contain_text("Paused at e2e_debug.py:3 (breakpoint)", timeout=90_000)
    expect(bob.locator(".debug-vars")).to_contain_text("total")
    expect(bob.locator(".debug-current-line")).to_have_count(1)
    expect(bob.get_by_role("button", name="⤼ Step over")).to_be_disabled()  # editors watch

    dave.keyboard.press("F10")
    expect(status).to_contain_text("last step by dave")
    bob.locator(".debug-frame", has_text="<module>").click()
    bob.locator(".debug-var", has_text="nums").click()
    expect(bob.locator(".debug-vars")).to_contain_text("len()")

    click_margin(dave, 3)  # remove the breakpoint, then run to the end
    expect(dave.locator(".bp-glyph")).to_have_count(0)
    dave.keyboard.press("F5")
    expect(dave.locator(".debug-status")).to_contain_text("The program finished (exit code 0)")
    dave.get_by_role("tab", name="Console", exact=True).click()
    expect(dave.locator(".run").last).to_contain_text("sum 6")
