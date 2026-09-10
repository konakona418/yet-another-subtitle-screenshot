from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .tools import ffprobe_binary, run_command

TEXT_SUBTITLE_CODECS = frozenset(
    {"ass", "ssa", "subrip", "srt", "mov_text", "webvtt", "text", "eia_608"}
)

FONT_MIMETYPES = frozenset(
    {
        "application/x-truetype-font",
        "application/x-font-ttf",
        "application/font-sfnt",
        "application/vnd.ms-opentype",
        "application/x-font-otf",
        "font/ttf",
        "font/otf",
    }
)

FONT_EXTENSIONS = frozenset({".ttf", ".otf", ".ttc", ".woff", ".woff2"})


class ProbeError(RuntimeError):
    """Raised when a media file cannot be inspected."""


@dataclass(frozen=True)
class SubtitleTrack:
    index: int
    ordinal: int
    codec: str
    language: str = ""
    title: str = ""
    default: bool = False
    forced: bool = False

    @property
    def is_text(self) -> bool:
        return self.codec in TEXT_SUBTITLE_CODECS

    @property
    def label(self) -> str:
        parts = [f"#{self.ordinal}"]
        if self.language:
            parts.append(self.language)
        parts.append(self.codec)
        flags = [name for name, value in (("default", self.default), ("forced", self.forced)) if value]
        if flags:
            parts.append(f"[{','.join(flags)}]")
        if self.title:
            parts.append(self.title)
        return " ".join(parts)


@dataclass(frozen=True)
class FontAttachment:
    index: int
    filename: str


@dataclass(frozen=True)
class MediaInfo:
    path: Path
    duration: float
    width: int
    height: int
    subtitle_tracks: tuple[SubtitleTrack, ...]
    font_attachments: tuple[FontAttachment, ...]

    @property
    def text_subtitle_tracks(self) -> tuple[SubtitleTrack, ...]:
        return tuple(track for track in self.subtitle_tracks if track.is_text)

    @property
    def bitmap_subtitle_tracks(self) -> tuple[SubtitleTrack, ...]:
        return tuple(track for track in self.subtitle_tracks if not track.is_text)

    def default_track(self) -> SubtitleTrack | None:
        tracks = self.text_subtitle_tracks
        if not tracks:
            return None
        for track in tracks:
            if track.default:
                return track
        return tracks[0]


def probe(path: str | Path) -> MediaInfo:
    source = Path(path)
    if not source.is_file():
        raise ProbeError(f"File not found: {source}")

    command = [
        ffprobe_binary(),
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(source),
    ]
    completed = run_command(command)
    if completed.returncode != 0:
        raise ProbeError(completed.stderr.strip() or f"ffprobe failed for {source}")

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError(f"Could not parse ffprobe output: {exc}") from exc

    streams = payload.get("streams", [])
    video = _first_video_stream(streams)
    if video is None:
        raise ProbeError(f"No video stream found in {source.name}")

    duration = _duration(payload, video)
    if duration <= 0:
        raise ProbeError(f"Could not determine the duration of {source.name}")

    subtitle_tracks: list[SubtitleTrack] = []
    font_attachments: list[FontAttachment] = []
    ordinal = 0
    for stream in streams:
        codec_type = stream.get("codec_type")
        tags = stream.get("tags", {}) or {}
        disposition = stream.get("disposition", {}) or {}
        if codec_type == "subtitle":
            subtitle_tracks.append(
                SubtitleTrack(
                    index=int(stream["index"]),
                    ordinal=ordinal,
                    codec=str(stream.get("codec_name", "unknown")),
                    language=str(tags.get("language", "")),
                    title=str(tags.get("title", "")),
                    default=bool(disposition.get("default")),
                    forced=bool(disposition.get("forced")),
                )
            )
            ordinal += 1
        elif codec_type == "attachment" and _is_font(tags):
            font_attachments.append(
                FontAttachment(
                    index=int(stream["index"]),
                    filename=str(tags.get("filename", f"font-{stream['index']}")),
                )
            )

    return MediaInfo(
        path=source,
        duration=duration,
        width=int(video.get("width", 0)),
        height=int(video.get("height", 0)),
        subtitle_tracks=tuple(subtitle_tracks),
        font_attachments=tuple(font_attachments),
    )


def _first_video_stream(streams: list[dict]) -> dict | None:
    for stream in streams:
        if stream.get("codec_type") != "video":
            continue
        disposition = stream.get("disposition", {}) or {}
        if disposition.get("attached_pic"):
            continue
        return stream
    return None


def _duration(payload: dict, video: dict) -> float:
    for value in (payload.get("format", {}).get("duration"), video.get("duration")):
        if value in (None, "N/A"):
            continue
        try:
            return round(float(value), 3)
        except (TypeError, ValueError):
            continue
    return 0.0


def _is_font(tags: dict) -> bool:
    mimetype = str(tags.get("mimetype", "")).lower()
    filename = str(tags.get("filename", ""))
    return mimetype in FONT_MIMETYPES or Path(filename).suffix.lower() in FONT_EXTENSIONS
