from __future__ import annotations
from pathlib import Path

PROMPT_DIR = Path(__file__).with_name('prompts')

def load_prompt(name: str) -> str:
    path = PROMPT_DIR / name
    if path.suffix != '.md' or path.parent != PROMPT_DIR or not path.exists():
        raise FileNotFoundError(f'prompt not found: {name}')
    return path.read_text(encoding='utf-8')
