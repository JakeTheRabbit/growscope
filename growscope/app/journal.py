"""Journal: manual entries plus automatic events from watched entity state changes.

Watches are {entity_id: grow_id} in settings. When a watched entity's recorded
state changes (crop steering phase moves P1 to P2, a valve alert flips on), an
event lands on that grow's timeline without anyone typing."""
from __future__ import annotations

import logging
from datetime import datetime

from . import db

_LOG = logging.getLogger("journal")

_last_states: dict[str, str] = {}


def watches() -> dict[str, int]:
    return {k: int(v) for k, v in db.get_setting("journal_watches", {}).items()}


def set_watches(mapping: dict[str, int]) -> None:
    db.set_setting("journal_watches", {k: int(v) for k, v in mapping.items() if k})


def observe(states: list[dict]) -> None:
    """Called by the recorder loop with each batch of polled states."""
    watched = watches()
    for s in states:
        entity, state = s.get("entity_id", ""), s.get("state", "")
        if not entity or not state or entity not in watched:
            continue
        previous = _last_states.get(entity)
        _last_states[entity] = state
        if previous is None or previous == state:
            continue
        grow = db.grow(watched[entity])
        if not grow or grow["status"] != "active":
            continue
        db.add_journal(grow["id"], datetime.now().isoformat(timespec="seconds"),
                       "state_change", f"{entity}: {previous} -> {state}")
        _LOG.info("auto-journal for %s: %s %s -> %s", grow["name"], entity, previous, state)
