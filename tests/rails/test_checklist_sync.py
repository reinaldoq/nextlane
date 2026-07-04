"""Anti-drift guard (Phase-2 Task 7, review item M5): `rails.agents.loop`
carries an inline `CHECKLIST` string literal that the cross-vendor reviewer
prompt (`rails.prompts.compose_review`) embeds verbatim. The human-oriented
`.claude/skills/domain-reviewer/SKILL.md` reproduces that SAME constant
verbatim in its own "Automated cross-vendor reviewer checklist" section, so
the two checklists can never silently diverge.

This test is the enforcement: every non-empty line of `CHECKLIST` must
appear, character for character, somewhere in the skill file's text. If
someone edits the constant in loop.py without updating the skill (or vice
versa), this fails instead of the two quietly drifting apart.
"""

from __future__ import annotations

from pathlib import Path

from rails.agents.loop import CHECKLIST

_SKILL_PATH = (
    Path(__file__).resolve().parents[2] / ".claude" / "skills" / "domain-reviewer" / "SKILL.md"
)


def test_skill_file_exists():
    assert _SKILL_PATH.is_file(), f"expected the domain-reviewer skill at {_SKILL_PATH}"


def test_every_checklist_line_appears_in_domain_reviewer_skill():
    skill_text = _SKILL_PATH.read_text(encoding="utf-8")
    lines = [line for line in CHECKLIST.splitlines() if line.strip()]
    assert lines, "CHECKLIST in rails.agents.loop must not be empty"

    missing = [line for line in lines if line not in skill_text]
    assert not missing, (
        "rails.agents.loop.CHECKLIST has drifted from "
        ".claude/skills/domain-reviewer/SKILL.md -- the following line(s) from the "
        "loop's CHECKLIST constant are missing verbatim from the skill file:\n"
        + "\n".join(f"  {line!r}" for line in missing)
    )
