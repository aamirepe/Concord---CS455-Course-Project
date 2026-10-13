"""Line-level change stats for the recent-changes panel.

Counting works like ``git diff --stat``: a line edited in place counts as one
removed and one added. Stats are taken over an attribution window (see
``checkpoint.py``) rather than per keystroke, so typing a line counts once.
"""
from __future__ import annotations

import difflib


def line_stats(old: str, new: str) -> tuple[int, int]:
    """Return ``(lines_added, lines_removed)`` to get from ``old`` to ``new``."""
    if old == new:
        return 0, 0
    a, b = old.splitlines(), new.splitlines()
    added = removed = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag in ("replace", "delete"):
            removed += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    return added, removed


def split_stats(weights: dict[int, int], added: int, removed: int) -> dict[int, tuple[int, int]]:
    """Share a window's stats among its authors.

    With one author (the usual case) they get everything. When several people
    edited the file in the same window, the diff can't say whose line is
    whose, so each gets a share proportional to how much they typed
    (``weights``: bytes of CRDT updates). Totals are preserved exactly.
    """
    if not weights:
        return {}
    if len(weights) == 1:
        return {next(iter(weights)): (added, removed)}
    return {
        uid: (a, r)
        for (uid, a), (_, r) in zip(
            _largest_remainder(weights, added).items(), _largest_remainder(weights, removed).items(), strict=True
        )
    }


def _largest_remainder(weights: dict[int, int], total: int) -> dict[int, int]:
    whole = sum(weights.values()) or 1
    exact = {uid: total * w / whole for uid, w in weights.items()}
    shares = {uid: int(v) for uid, v in exact.items()}
    leftover = total - sum(shares.values())
    for uid in sorted(exact, key=lambda u: exact[u] - shares[u], reverse=True)[:leftover]:
        shares[uid] += 1
    return shares
