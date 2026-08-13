from __future__ import annotations

from pathlib import Path


def _strip_inline_comment(value: str) -> str:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote == '"':
            escaped = True
            continue
        if char in {'"', "'"}:
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
            continue
        if char == "#" and quote is None and (
            index == 0 or value[index - 1].isspace()
        ):
            return value[:index].rstrip()
    return value.strip()


def parse_dotenv(path: str | Path) -> dict[str, str]:
    dotenv = Path(path)
    if not dotenv.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in dotenv.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        value = _strip_inline_comment(raw_value.strip())
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key.strip()] = value
    return values
