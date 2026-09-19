"""Runtime contract for the bundled JobScout skill."""

from dataclasses import dataclass
from pathlib import Path

from deerflow.skills.parser import parse_skill_file
from deerflow.skills.tool_policy import filter_tools_by_skill_allowed_tools
from deerflow.skills.types import SkillCategory


REPO_ROOT = Path(__file__).resolve().parents[2]
JOBSCOUT_SKILL_FILE = REPO_ROOT / "skills" / "public" / "jobscout" / "SKILL.md"


@dataclass(frozen=True)
class _Tool:
    name: str


def test_jobscout_exposes_research_tools_but_not_clarification() -> None:
    """A complete request must not be interruptible by generic clarification."""
    skill = parse_skill_file(JOBSCOUT_SKILL_FILE, category=SkillCategory.PUBLIC)

    assert skill is not None
    tools = [
        _Tool("web_search"),
        _Tool("web_fetch"),
        _Tool("read_file"),
        _Tool("task"),
        _Tool("ask_clarification"),
        _Tool("bash"),
    ]

    filtered = filter_tools_by_skill_allowed_tools(tools, [skill])

    assert {tool.name for tool in filtered} == {
        "web_search",
        "web_fetch",
        "read_file",
        "task",
    }
