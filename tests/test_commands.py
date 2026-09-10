from pathlib import Path

import pytest

from yass import extract
from yass.extract import build_frame_command, escape_filter_value, jpeg_qscale
from yass.subs import PreparedSubtitle


@pytest.fixture(autouse=True)
def _fake_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(extract, "ffmpeg_binary", lambda: "ffmpeg")


def test_build_frame_command_basic() -> None:
    command = build_frame_command(Path("/videos/show.mkv"), 83.456, Path("/out/still.png"))
    assert command[0] == "ffmpeg"
    assert command[command.index("-ss") + 1] == "83.456"
    assert "-copyts" in command
    assert command[command.index("-i") + 1] == "/videos/show.mkv"
    assert command[command.index("-frames:v") + 1] == "1"
    assert command[-1] == "/out/still.png"
    assert "-vf" not in command


def test_build_frame_command_with_subtitles() -> None:
    subtitle = PreparedSubtitle(path=Path("/tmp/yass-123/subs.ass"))
    command = build_frame_command(
        Path("/videos/show.mkv"),
        10.0,
        Path("/out/still.png"),
        subtitle=subtitle,
        fonts_dir=Path("/tmp/yass-123/fonts"),
    )
    filter_arg = command[command.index("-vf") + 1]
    assert filter_arg == (
        "subtitles=filename=/tmp/yass-123/subs.ass:fontsdir=/tmp/yass-123/fonts"
    )


def test_build_frame_command_jpeg_quality() -> None:
    command = build_frame_command(
        Path("/videos/show.mkv"),
        10.0,
        Path("/out/still.jpg"),
        image_format="jpg",
        jpeg_quality=90,
    )
    assert command[command.index("-q:v") + 1] == str(jpeg_qscale(90))


@pytest.mark.parametrize(
    ("quality", "expected"),
    [(1, 31), (100, 2), (0, 31), (101, 2)],
)
def test_jpeg_qscale_bounds(quality: int, expected: int) -> None:
    assert jpeg_qscale(quality) == expected


def test_escape_filter_value() -> None:
    assert escape_filter_value("/tmp/plain/subs.ass") == "/tmp/plain/subs.ass"
    assert escape_filter_value("/a,b:c'subs.ass") == "/a\\,b\\:c\\'subs.ass"
