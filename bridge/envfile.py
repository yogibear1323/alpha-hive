"""Update gitignored .env files without printing values."""
from __future__ import annotations

from pathlib import Path


def upsert_env(path: Path, key: str, value: str) -> None:
    """Set `key=value` in a .env file, replacing a previous assignment of `key`.

    The value is written as-is; callers must not log it. Creates the file if needed.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    found = False
    if path.exists():
        raw = path.read_text(encoding="utf-8")
        for line in raw.splitlines():
            stripped = line.strip()
            if stripped.startswith(f"{key}=") and not stripped.startswith("#"):
                if not found:
                    lines.append(f"{key}={value}")
                    found = True
                continue
            lines.append(line)
    if not found:
        if lines and lines[-1] != "":
            lines.append("")
        lines.append(f"{key}={value}")
    text = "\n".join(lines)
    if not text.endswith("\n"):
        text += "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
