ALLOWED: dict[str, set[str]] = {
    "available": {"reserved", "sold"},
    "reserved": {"available", "sold"},
    "sold": set(),
}


def can_transition(frm: str, to: str) -> bool:
    return to in ALLOWED.get(frm, set())
