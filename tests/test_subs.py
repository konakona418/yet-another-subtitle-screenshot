import subprocess
from pathlib import Path

import pytest

from yass import subs
from yass.ffprobe import FontAttachment
from yass.subs import PreparedSubtitle, parse_cue_intervals

ASS_TEXT = """\
[Script Info]
Title: test

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:03.50,Default,,0,0,0,,Hello
Dialogue: 0,0:01:05.25,0:01:08.00,Default,,0,0,0,,World
"""

SRT_TEXT = """\
1
00:00:01,000 --> 00:00:03,500
Hello

2
00:01:05,250 --> 00:01:08,000
World
"""


def test_parse_ass_intervals() -> None:
    assert parse_cue_intervals(ASS_TEXT, ".ass") == ((1.0, 3.5), (65.25, 68.0))


def test_parse_srt_intervals() -> None:
    assert parse_cue_intervals(SRT_TEXT, ".srt") == ((1.0, 3.5), (65.25, 68.0))


def test_parse_unknown_suffix_returns_none() -> None:
    assert parse_cue_intervals(SRT_TEXT, ".txt") is None


def test_is_active_boundaries() -> None:
    subtitle = PreparedSubtitle(path=Path("/tmp/subs.srt"), intervals=((1.0, 3.5),))
    assert subtitle.is_active(0.9) is False
    assert subtitle.is_active(1.0) is True
    assert subtitle.is_active(3.49) is True
    assert subtitle.is_active(3.5) is False


def test_is_active_unknown_when_unparsed() -> None:
    subtitle = PreparedSubtitle(path=Path("/tmp/subs.srt"), intervals=None)
    assert subtitle.is_active(1.0) is None


def test_safe_font_name_sanitizes_unsafe_names() -> None:
    assert subs._safe_font_name(FontAttachment(index=22, filename="Blue Highway.ttf")) == (
        "font_022.ttf"
    )
    assert subs._safe_font_name(FontAttachment(index=3, filename="a/b.otf")) == "font_003.otf"
    assert subs._safe_font_name(FontAttachment(index=4, filename="weird.exe")) == "font_004.ttf"


def test_extract_fonts_uses_explicit_safe_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    attachments = [FontAttachment(index=22, filename="Blue Highway.ttf")]

    def fake_run(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if cwd is not None:
            (cwd / "font_022.ttf").write_bytes(b"font")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subs, "ffmpeg_binary", lambda: "ffmpeg")
    monkeypatch.setattr(subs, "run_command", fake_run)

    subs.extract_fonts(Path("/videos/show.mkv"), tmp_path, attachments)

    assert len(calls) == 1
    assert "-dump_attachment:22" in calls[0]
    assert "font_022.ttf" in calls[0]
    assert (tmp_path / "font_022.ttf").exists()


def test_extract_fonts_retries_failed_attachments_individually(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    attachments = [
        FontAttachment(index=3, filename="a.ttf"),
        FontAttachment(index=4, filename="b.otf"),
    ]

    def fake_run(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if cwd is None:
            return subprocess.CompletedProcess(command, 0, "", "")
        combined = "-dump_attachment:3" in command and "-dump_attachment:4" in command
        if combined:
            return subprocess.CompletedProcess(command, 1, "", "boom")
        if "font_003.ttf" in command:
            (cwd / "font_003.ttf").write_bytes(b"font")
        if "font_004.otf" in command:
            (cwd / "font_004.otf").write_bytes(b"font")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subs, "ffmpeg_binary", lambda: "ffmpeg")
    monkeypatch.setattr(subs, "run_command", fake_run)

    subs.extract_fonts(Path("/videos/show.mkv"), tmp_path, attachments)

    assert len(calls) == 3
    assert (tmp_path / "font_003.ttf").exists()
    assert (tmp_path / "font_004.otf").exists()


def test_extract_fonts_raises_when_nothing_dumped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, "", "boom")

    monkeypatch.setattr(subs, "ffmpeg_binary", lambda: "ffmpeg")
    monkeypatch.setattr(subs, "run_command", fake_run)

    with pytest.raises(subs.SubtitleError):
        subs.extract_fonts(
            Path("/videos/show.mkv"), tmp_path, [FontAttachment(index=3, filename="a.ttf")]
        )
