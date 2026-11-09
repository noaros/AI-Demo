from dataclasses import dataclass
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


@dataclass(frozen=True)
class Prompt:
    """A system prompt loaded from prompts/<name>.md. Files are never edited in place: a change is a new file."""

    name: str
    text: str


def load_prompt(name: str) -> Prompt:
    # Read once at startup, so a missing or misspelled prompt fails the deploy, not a user's request.
    return Prompt(name=name, text=(PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8").strip())
