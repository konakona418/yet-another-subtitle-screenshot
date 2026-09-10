from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TimestampEntry:
    seconds: float
    raw: str
    line: int
    in_range: bool = True


@dataclass
class ParsedTimestamps:
    entries: list[TimestampEntry] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def parse_timestamp(text: str) -> float:
    """Parse a loose timestamp into seconds.

    Accepted forms: ``HH:MM:SS.mmm``, ``HH:MM:SS``, ``MM:SS``, ``SS`` and
    decimal seconds with either ``.`` or ``,`` as the separator.
    """
    raw = text.strip()
    if not raw:
        raise ValueError("empty timestamp")

    parts = raw.split(":")
    if len(parts) > 3:
        raise ValueError(f"invalid timestamp: {raw!r}")

    numbers: list[float] = []
    for index, part in enumerate(parts):
        if not part:
            raise ValueError(f"invalid timestamp: {raw!r}")
        if index < len(parts) - 1 and ("." in part or "," in part):
            raise ValueError(f"invalid timestamp: {raw!r}")
        try:
            numbers.append(float(part.replace(",", ".")))
        except ValueError:
            raise ValueError(f"invalid timestamp: {raw!r}") from None

    if len(parts) == 3:
        hours, minutes, seconds = numbers
        if minutes >= 60 or seconds >= 60:
            raise ValueError(f"invalid timestamp: {raw!r}")
        total = hours * 3600 + minutes * 60 + seconds
    elif len(parts) == 2:
        minutes, seconds = numbers
        if seconds >= 60:
            raise ValueError(f"invalid timestamp: {raw!r}")
        total = minutes * 60 + seconds
    else:
        total = numbers[0]

    if total < 0:
        raise ValueError(f"invalid timestamp: {raw!r}")
    return round(total, 3)


def format_timestamp(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def format_filename(seconds: float) -> str:
    return format_timestamp(seconds).replace(":", "-").replace(".", "-")


def parse_timestamp_list(text: str, duration: float | None = None) -> ParsedTimestamps:
    """Parse one timestamp per line, sort them and drop duplicates."""
    result = ParsedTimestamps()
    parsed: list[tuple[float, str, int]] = []

    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            parsed.append((parse_timestamp(stripped), stripped, lineno))
        except ValueError:
            result.errors.append(f"Line {lineno}: invalid timestamp {stripped!r}")

    parsed.sort(key=lambda item: item[0])

    seen: set[int] = set()
    for seconds, raw, lineno in parsed:
        key = round(seconds * 1000)
        if key in seen:
            result.warnings.append(f"Line {lineno}: duplicate timestamp {raw!r} ignored")
            continue
        seen.add(key)
        in_range = duration is None or seconds < duration
        if not in_range and duration is not None:
            result.warnings.append(
                f"Line {lineno}: {raw} is beyond the video duration "
                f"({format_timestamp(duration)}); skipped"
            )
        result.entries.append(
            TimestampEntry(seconds=seconds, raw=raw, line=lineno, in_range=in_range)
        )

    return result
