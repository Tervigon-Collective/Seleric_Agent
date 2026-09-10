"""In-process registry of mission ids the office gateway can list.

The mission stores (in-memory / postgres) have no "list every mission" method
and adding one is out of scope for a read-only UI bridge. Instead ``main.py``
calls :func:`register_mission` whenever a mission is accepted, so the office
``/v1/office/missions`` endpoint can enumerate recent missions without changing
the persistence contract. Bounded so a long-lived process cannot leak.
"""

from __future__ import annotations

from collections import OrderedDict
from threading import Lock

_MAX = 200
_lock = Lock()
_ids: OrderedDict[str, None] = OrderedDict()


def register_mission(mission_id: str | None) -> None:
    if not mission_id:
        return
    with _lock:
        if mission_id in _ids:
            _ids.move_to_end(mission_id)
        else:
            _ids[mission_id] = None
        while len(_ids) > _MAX:
            _ids.popitem(last=False)


def known_mission_ids() -> list[str]:
    """Most-recently-registered first."""
    with _lock:
        return list(reversed(_ids.keys()))


def clear() -> None:  # test helper
    with _lock:
        _ids.clear()
