from collections.abc import Mapping, Sequence
from statistics import median
from typing import Any

MISSING = "-"


def get(payload: Mapping[str, Any], path: str) -> Any:
    """Read a dotted path, returning None when any step is absent."""
    current: Any = payload
    for step in path.split("."):
        if not isinstance(current, Mapping) or step not in current:
            return None
        current = current[step]
    return current


def has(payload: Mapping[str, Any], path: str) -> bool:
    """Whether every step of a dotted path exists, even when the value is None."""
    current: Any = payload
    for step in path.split("."):
        if not isinstance(current, Mapping) or step not in current:
            return False
        current = current[step]
    return True


def stats(values: Sequence[float]) -> dict[str, float] | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return {
        "median": float(median(present)),
        "minimum": float(min(present)),
        "maximum": float(max(present)),
        "samples": len(present),
    }


def agreement(values: Sequence[Any]) -> dict[str, Any] | None:
    """One value when every repetition said the same thing, the set when not."""
    present = [value for value in values if value is not None]
    if not present:
        return None
    distinct = list(dict.fromkeys(str(value) for value in present))
    return {
        "value": distinct[0] if len(distinct) == 1 else None,
        "distinct": distinct,
        "agrees": len(distinct) == 1,
        "samples": len(present),
    }


def number_cell(summary: Mapping[str, Any] | None, digits: int = 0) -> str:
    if summary is None:
        return MISSING
    text = f"{summary['median']:.{digits}f}"
    if summary["minimum"] != summary["maximum"]:
        low = f"{summary['minimum']:.{digits}f}"
        high = f"{summary['maximum']:.{digits}f}"
        return f"{text} ({low} to {high})"
    return text


def text_cell(summary: Mapping[str, Any] | None, limit: int = 80) -> str:
    if summary is None:
        return MISSING
    if summary["agrees"]:
        return escape(truncate(summary["value"], limit))
    joined = ", ".join(truncate(value, limit) for value in summary["distinct"])
    return f"varies: {escape(joined)}"


def truncate(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else f"{text[: limit - 3]}..."


def escape(text: str) -> str:
    return str(text).replace("|", "\\|")


def table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return lines
