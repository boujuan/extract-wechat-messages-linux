"""tiktoken-based token counting.

cl100k_base is OpenAI's GPT-4 / GPT-3.5 BPE; for Anthropic's Claude models
it tends to be within ±5% on Latin scripts. Good enough for context-window
budgeting.
"""
from __future__ import annotations

import functools
from pathlib import Path

import tiktoken


@functools.lru_cache(maxsize=1)
def _encoder() -> "tiktoken.Encoding":
    return tiktoken.get_encoding("cl100k_base")


def count(text: str) -> int:
    if not text:
        return 0
    return len(_encoder().encode(text, disallowed_special=()))


def count_file(path: Path) -> int:
    return count(path.read_text(encoding="utf-8"))


def fmt_short(n: int) -> str:
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}k".rstrip("0").rstrip(".")
    return f"{n / 1_000_000:.1f}M"
