"""CRDT helpers — thin wrappers around pycrdt so the rest of the codebase
doesn't import pycrdt types directly.

The collaboration model:
- One ``pycrdt.Doc`` per (project_id, file_id) lives in the room manager.
- All clients in that room apply updates to the same ``Doc``.
- New clients receive a full state on connect.
- Reconnecting clients send their state vector; the server returns only the diff.

pycrdt is the Python binding for the same Rust core that powers Yjs in the browser,
so a Yjs client and pycrdt server converge correctly on the same document.

API notes for pycrdt 0.10.x
----------------------------
- ``Doc`` does **not** have ``get_text()`` or ``get_diff()`` — those were
  removed/renamed. Use ``doc.get(name, type=Text)`` and ``doc.get_update(sv)``.
- ``doc.get_state()`` returns the **state vector** (a 7-byte struct: client_id
  + clock), not the full state. To get the full state for a new client, use
  ``doc.get_update()`` with no argument.
- This is the opposite of how the Yjs JS API behaves, where ``encodeStateAsUpdate``
  is full state and the state vector needs a separate method. We abstract that
  difference here so the rest of the codebase doesn't have to know.
"""
from __future__ import annotations

from pycrdt import Doc, Text

# The text type name. Monaco's y-monaco binding uses "codemirror" or "monaco" depending
# on configuration; we standardize on "monaco" since that's what we use.
TEXT_TYPE = "monaco"


def new_doc(initial_text: str = "") -> Doc:
    """Create a new CRDT doc with a single text field populated with initial_text."""
    doc: Doc = Doc()
    text = doc.get(TEXT_TYPE, type=Text)
    if initial_text:
        text.insert(0, initial_text)
    return doc


def doc_from_bytes(payload: bytes) -> Doc:
    """Restore a Doc from a full-state payload (e.g. from a snapshot).

    A full-state payload is produced by ``doc_to_bytes``; it is the result of
    ``doc.get_update()`` with no argument.
    """
    doc: Doc = Doc()
    if payload:
        doc.apply_update(payload)
    return doc


def doc_to_bytes(doc: Doc) -> bytes:
    """Encode a Doc's full state as a binary update for storage or snapshot.

    In pycrdt 0.10.x this is ``doc.get_update()`` (no argument) — NOT
    ``doc.get_state()``, which returns only the state vector.
    """
    return bytes(doc.get_update())


def doc_to_text(doc: Doc) -> str:
    return str(doc.get(TEXT_TYPE, type=Text))


def apply_update(doc: Doc, update: bytes) -> None:
    doc.apply_update(update)


def apply_update_counting(doc: Doc, update: bytes) -> int:
    """Apply ``update`` and return how many characters it inserted plus deleted.

    Used to weigh authors' shares of a change window by what they actually
    typed, rather than by update size (a keystroke's update is mostly
    overhead, so size would favour whoever types in many small updates).
    """
    changed = 0

    def count(event) -> None:
        nonlocal changed
        for op in event.delta:
            changed += len(op.get("insert", "")) if isinstance(op.get("insert"), str) else 0
            changed += op.get("delete", 0)

    text = doc.get(TEXT_TYPE, type=Text)
    sub = text.observe(count)
    try:
        doc.apply_update(update)
    finally:
        text.unobserve(sub)
    return changed


def state_vector(doc: Doc) -> bytes:
    """Return the doc's state vector (a compact summary of what the doc has).

    In pycrdt 0.10.x this is ``doc.get_state()``.
    """
    return bytes(doc.get_state())


def diff_update(doc: Doc, remote_state_vector: bytes) -> bytes:
    """Produce an update that brings ``remote_state_vector`` up to ``doc``'s state.

    If ``remote_state_vector`` is empty, returns the full state.
    """
    if not remote_state_vector:
        return bytes(doc.get_update())
    return bytes(doc.get_update(remote_state_vector))
