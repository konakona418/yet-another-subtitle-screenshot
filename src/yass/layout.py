from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


class Direction(str, Enum):
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"
    GRID = "grid"
    STACK = "stack"


@dataclass(frozen=True)
class LayoutSpec:
    direction: Direction = Direction.GRID
    columns: int = 3
    gap: int = 10
    margin: int = 0
    background: tuple[int, int, int] = (0, 0, 0)
    show_labels: bool = False
    strip: int = 160


def grid_shape(count: int, direction: Direction, columns: int) -> tuple[int, int]:
    """Return the (columns, rows) shape for ``count`` tiles."""
    if count <= 0:
        raise ValueError("count must be positive")
    if direction is Direction.HORIZONTAL:
        return count, 1
    if direction in (Direction.VERTICAL, Direction.STACK):
        return 1, count
    cols = max(1, min(columns, count))
    rows = -(-count // cols)
    return cols, rows


def tile_positions(
    count: int,
    tile_w: int,
    tile_h: int,
    spec: LayoutSpec,
) -> list[tuple[int, int]]:
    if spec.direction is Direction.STACK:
        # Each tile is offset down by ``strip`` pixels so only its bottom
        # strip stays visible once the previous tile is pasted on top.
        return [(spec.margin, spec.margin + index * spec.strip) for index in range(count)]

    columns, _ = grid_shape(count, spec.direction, spec.columns)
    positions: list[tuple[int, int]] = []
    for index in range(count):
        column = index % columns
        row = index // columns
        x = spec.margin + column * (tile_w + spec.gap)
        y = spec.margin + row * (tile_h + spec.gap)
        positions.append((x, y))
    return positions


def canvas_size(count: int, tile_w: int, tile_h: int, spec: LayoutSpec) -> tuple[int, int]:
    if spec.direction is Direction.STACK:
        width = spec.margin * 2 + tile_w
        height = spec.margin * 2 + tile_h + (count - 1) * spec.strip
        return width, height

    columns, rows = grid_shape(count, spec.direction, spec.columns)
    width = spec.margin * 2 + columns * tile_w + (columns - 1) * spec.gap
    height = spec.margin * 2 + rows * tile_h + (rows - 1) * spec.gap
    return width, height


def compose(
    paths: list[Path],
    spec: LayoutSpec,
    labels: list[str] | None = None,
    tile_size: tuple[int, int] | None = None,
) -> Image.Image:
    if not paths:
        raise ValueError("no images to compose")

    with Image.open(paths[0]) as first:
        default_size = first.size
    tile_w, tile_h = tile_size or default_size

    canvas = Image.new(
        "RGB",
        canvas_size(len(paths), tile_w, tile_h, spec),
        spec.background,
    )
    positions = tile_positions(len(paths), tile_w, tile_h, spec)
    order = reversed(list(zip(paths, positions))) if spec.direction is Direction.STACK else zip(paths, positions)
    for path, position in order:
        with Image.open(path) as image:
            canvas.paste(image.convert("RGB"), position)

    if spec.show_labels and labels:
        _draw_labels(canvas, labels, positions, tile_w, tile_h)

    return canvas


def _draw_labels(
    canvas: Image.Image,
    labels: list[str],
    positions: list[tuple[int, int]],
    tile_w: int,
    tile_h: int,
) -> None:
    font_size = max(12, tile_h // 30)
    font = _load_font(font_size)
    padding = max(4, font_size // 4)
    draw = ImageDraw.Draw(canvas)

    for label, (x, y) in zip(labels, positions):
        bbox = draw.textbbox((0, 0), label, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        box = (
            x + 6,
            y + tile_h - text_h - padding * 2 - 6,
            x + 6 + text_w + padding * 2,
            y + tile_h - 6,
        )
        draw.rectangle(box, fill=(0, 0, 0))
        draw.text((box[0] + padding, box[1] + padding - bbox[1]), label, fill=(255, 255, 255), font=font)


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/Library/Fonts/Arial.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).is_file():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    return ImageFont.load_default(size)
