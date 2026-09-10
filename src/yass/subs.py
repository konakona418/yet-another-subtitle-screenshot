from __future__ import annotations

import re
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .ffprobe import FONT_EXTENSIONS, FontAttachment, SubtitleTrack
from .tools import ffmpeg_binary, run_command

SUBTITLE_EXTENSIONS = frozenset({".ass", ".ssa", ".srt"})

_ASS_TIME_RE = re.compile(r"(\d+):(\d{2}):(\d{2})[.,](\d{1,3})")


class SubtitleError(RuntimeError):
    """Raised when a subtitle track or font cannot be prepared."""


@dataclass(frozen=True)
class PreparedSubtitle:
    path: Path
    intervals: tuple[tuple[float, float], ...] | None = None

    def is_active(self, seconds: float) -> bool | None:
        """Whether a cue is displayed at ``seconds``; ``None`` when unknown."""
        if self.intervals is None:
            return None
        return any(start <= seconds < end for start, end in self.intervals)


def prepare_subtitle(
    video: Path,
    track: SubtitleTrack | None,
    external: Path | None,
    tmpdir: Path,
) -> PreparedSubtitle:
    if external is not None:
        path = _copy_external(external, tmpdir)
    elif track is not None:
        path = _extract_track(video, track, tmpdir)
    else:
        raise ValueError("either track or external must be provided")

    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        raise SubtitleError(f"Could not read subtitle file: {exc}") from exc

    return PreparedSubtitle(path=path, intervals=parse_cue_intervals(text, path.suffix.lower()))


def extract_fonts(
    video: Path,
    dest_dir: Path,
    attachments: Sequence[FontAttachment],
) -> Path:
    """Dump embedded font attachments into ``dest_dir`` and return it.

    ffmpeg refuses metadata filenames it considers unsafe (spaces, path
    separators) and aborts the whole input when it hits one, so every
    attachment is dumped under a generated ASCII name instead.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    pending = {attachment.index: _safe_font_name(attachment) for attachment in attachments}

    if pending and _dump_attachments(video, dest_dir, pending):
        pending = {
            index: name for index, name in pending.items() if not (dest_dir / name).exists()
        }

    for index, name in pending.items():
        if not (dest_dir / name).exists():
            _dump_attachments(video, dest_dir, {index: name})

    if not any(dest_dir.iterdir()):
        raise SubtitleError("failed to extract embedded fonts")
    return dest_dir


def _safe_font_name(attachment: FontAttachment) -> str:
    suffix = Path(attachment.filename).suffix.lower()
    if suffix not in FONT_EXTENSIONS:
        suffix = ".ttf"
    return f"font_{attachment.index:03d}{suffix}"


def _dump_attachments(video: Path, dest_dir: Path, names: dict[int, str]) -> bool:
    # Dumping attachments while the default streams are mapped makes ffmpeg
    # decode the entire video, which is needlessly slow. Disabling the real
    # streams avoids that; the silent dummy input keeps ffmpeg satisfied that
    # the output has at least one stream.
    command = [
        ffmpeg_binary(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-an",
        "-vn",
        "-sn",
    ]
    for index, name in names.items():
        command += [f"-dump_attachment:{index}", name]
    command += [
        "-i",
        str(video),
        "-f",
        "lavfi",
        "-i",
        "anullsrc",
        "-map",
        "1:a",
        "-t",
        "0",
        "-f",
        "null",
        "-",
    ]
    completed = run_command(command, cwd=dest_dir)
    return completed.returncode == 0


def parse_cue_intervals(text: str, suffix: str) -> tuple[tuple[float, float], ...] | None:
    try:
        if suffix in {".ass", ".ssa"}:
            return _parse_ass(text)
        if suffix == ".srt":
            return _parse_srt(text)
    except Exception:
        return None
    return None


def _copy_external(external: Path, tmpdir: Path) -> Path:
    suffix = external.suffix.lower()
    if suffix not in SUBTITLE_EXTENSIONS:
        raise SubtitleError(f"Unsupported subtitle format: {external.suffix or external.name}")
    if not external.is_file():
        raise SubtitleError(f"Subtitle file not found: {external}")
    dest = tmpdir / f"subs{suffix}"
    shutil.copyfile(external, dest)
    return dest


def _extract_track(video: Path, track: SubtitleTrack, tmpdir: Path) -> Path:
    suffix = ".ass" if track.codec in {"ass", "ssa"} else ".srt"
    dest = tmpdir / f"subs{suffix}"
    codec = "copy" if suffix == ".ass" else "srt"
    command = [
        ffmpeg_binary(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(video),
        "-map",
        f"0:{track.index}",
        "-c:s",
        codec,
        str(dest),
    ]
    completed = run_command(command)
    if completed.returncode != 0 or not dest.is_file():
        raise SubtitleError(completed.stderr.strip() or "failed to extract the subtitle track")
    return dest


def _to_seconds(match: re.Match[str]) -> float:
    hours, minutes, seconds, fraction = match.groups()
    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(fraction.ljust(3, "0")) / 1000
    )


def _parse_ass(text: str) -> tuple[tuple[float, float], ...]:
    intervals: list[tuple[float, float]] = []
    for line in text.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        fields = line.split(",", 9)
        if len(fields) < 3:
            continue
        start = _ASS_TIME_RE.match(fields[1].strip())
        end = _ASS_TIME_RE.match(fields[2].strip())
        if start and end:
            intervals.append((_to_seconds(start), _to_seconds(end)))
    return tuple(intervals)


def _parse_srt(text: str) -> tuple[tuple[float, float], ...]:
    intervals: list[tuple[float, float]] = []
    for line in text.splitlines():
        if "-->" not in line:
            continue
        left, right = line.split("-->", 1)
        start = _ASS_TIME_RE.search(left)
        end = _ASS_TIME_RE.search(right)
        if start and end:
            intervals.append((_to_seconds(start), _to_seconds(end)))
    return tuple(intervals)
