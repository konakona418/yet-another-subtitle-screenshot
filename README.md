# Yet Another Subtitle Screenshot

A small desktop tool that extracts still frames from a video at chosen timestamps,
burns the subtitles that are displayed at those moments into the frames, and
assembles the stills into a contact sheet. Built with Python, tkinter, and FFmpeg
(libass) via `uv`.

## Features

- Works with any container FFmpeg can read; MKV with embedded text subtitles is
  the primary target.
- Embedded text subtitle tracks (ASS/SSA/SRT/WebVTT/`mov_text`) are listed and can
  be selected; external `.srt`/`.ass`/`.ssa` files are also supported.
- Embedded font attachments (common in anime MKV files) are extracted and passed
  to libass, so subtitle styling survives.
- Loose timestamp input (`HH:MM:SS.mmm`, `HH:MM:SS`, `MM:SS`, seconds). Input is
  sorted automatically and duplicates are removed.
- Still frames plus a contact sheet, with configurable layout:
  horizontal / vertical / grid / stack, column count, gap, outer margin,
  background colour, optional timestamp labels, PNG or JPEG output.
  In **stack** mode the first frame is shown in full and every following frame
  is offset downwards so only its bottom strip (usually the subtitle) stays
  visible; the strip height is adjustable.
- On-demand preview of a single timestamp and of the full composed layout, both
  in an adaptive window with wheel zoom and drag panning (no real-time video
  preview).
- Runs each timestamp as a fast, accurate seek; no full decode of the video.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- FFmpeg with `ffmpeg` and `ffprobe` on `PATH`, built with libass
  (`--enable-libass`)

On Linux the project prefers the system Python (`python-preference = "system"`
in `pyproject.toml`) because the uv-managed CPython builds ship a Tk compiled
without Xft, which renders text without antialiasing. Install your distro's
`tk` package (e.g. `sudo pacman -S tk` on Arch) so tkinter is available.

## Install

```sh
uv sync
```

## Run

```sh
uv run yass
```

1. Pick a video file. Its duration, resolution, and text subtitle tracks are shown.
2. Choose an embedded subtitle track, or load an external subtitle file.
3. Enter one timestamp per line in the Timestamps box.
4. Adjust the layout, output folder, and image format as needed.
5. Use **Preview frame** to render the timestamp under the caret, **Preview
   layout** to render and compose every timestamp in an adaptive window, or
   **Generate** to produce all stills and the contact sheet.

## Output

Each run creates a timestamped folder inside the chosen output root:

```
<video name>_<YYYYmmdd_HHMMSS>/
  001_00-01-23-456.png
  002_00-02-45-100.png
  contact_sheet.png
```

## Notes

- Bitmap subtitle tracks (PGS/VobSub) cannot be burned in by libass and are
  ignored.
- Timestamps beyond the video duration are skipped with a warning; timestamps
  where no subtitle is displayed still produce a still, with a warning.
- The seek strategy is `ffmpeg -ss T -copyts -i ... -vf subtitles=...`, which is
  both fast and frame-accurate.

## Tests

```sh
uv run pytest
```
