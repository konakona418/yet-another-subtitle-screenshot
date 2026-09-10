from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class ToolError(RuntimeError):
    """Raised when an external tool (ffmpeg/ffprobe) is missing or fails to run."""


def ffmpeg_binary() -> str:
    return _which("ffmpeg")


def ffprobe_binary() -> str:
    return _which("ffprobe")


def _which(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise ToolError(f"{name} was not found on PATH. Install FFmpeg and try again.")
    return path


def run_command(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
