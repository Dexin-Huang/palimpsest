from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = PROJECT_ROOT / "palimpsest" / "prompts"
PROMPT_SETS_DIR = PROMPTS_DIR / "sets"


def load_prompt(name: str) -> str:
    prompt_path = PROMPTS_DIR / f"{name}.txt"
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


def load_prompt_set(set_name: str) -> tuple[str, str]:
    set_dir = PROMPT_SETS_DIR / set_name
    pass1_path = set_dir / "pass1.txt"
    pass2_path = set_dir / "pass2.txt"
    if not pass1_path.exists() or not pass2_path.exists():
        raise FileNotFoundError(
            f"Prompt set not found: {set_dir} (expected pass1.txt and pass2.txt)"
        )
    return (
        pass1_path.read_text(encoding="utf-8"),
        pass2_path.read_text(encoding="utf-8"),
    )


def load_prompt_pair(
    prompt_name: Optional[str],
    prompt_set: Optional[str],
    prompt_pass1: Optional[str],
    prompt_pass2: Optional[str],
) -> tuple[str, str]:
    if prompt_pass1 and prompt_pass2:
        return (
            Path(prompt_pass1).read_text(encoding="utf-8"),
            Path(prompt_pass2).read_text(encoding="utf-8"),
        )
    if prompt_set:
        return load_prompt_set(prompt_set)
    if prompt_name:
        return (
            load_prompt(prompt_name),
            load_prompt(f"{prompt_name}_refine"),
        )
    raise FileNotFoundError("No prompt specified")
