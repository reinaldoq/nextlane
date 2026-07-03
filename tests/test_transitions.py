import pytest

from api._lib.transitions import can_transition

# Full matrix over the three known states plus transitions from/to unknown states.
ALLOWED_PAIRS = {
    ("available", "reserved"),
    ("available", "sold"),
    ("reserved", "available"),
    ("reserved", "sold"),
}

STATES = ["available", "reserved", "sold"]

CASES = [(frm, to, (frm, to) in ALLOWED_PAIRS) for frm in STATES for to in STATES]


@pytest.mark.parametrize("frm, to, expected", CASES)
def test_full_matrix(frm: str, to: str, expected: bool):
    assert can_transition(frm, to) is expected


def test_available_to_reserved_is_allowed():
    assert can_transition("available", "reserved") is True


def test_available_to_sold_is_allowed_direct_sale():
    assert can_transition("available", "sold") is True


def test_reserved_to_available_is_allowed_cancel():
    assert can_transition("reserved", "available") is True


def test_reserved_to_sold_is_allowed():
    assert can_transition("reserved", "sold") is True


@pytest.mark.parametrize("to", ["available", "reserved", "sold"])
def test_sold_to_anything_is_forbidden(to: str):
    assert can_transition("sold", to) is False


@pytest.mark.parametrize("state", ["available", "reserved", "sold"])
def test_same_state_is_forbidden(state: str):
    assert can_transition(state, state) is False


@pytest.mark.parametrize(
    "frm, to",
    [
        ("unknown", "available"),
        ("available", "unknown"),
        ("unknown", "unknown"),
        ("", "available"),
        ("available", ""),
    ],
)
def test_unknown_states_are_forbidden(frm: str, to: str):
    assert can_transition(frm, to) is False
