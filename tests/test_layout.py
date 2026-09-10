from pathlib import Path

import pytest
from PIL import Image

from yass.layout import Direction, LayoutSpec, canvas_size, compose, grid_shape, tile_positions


def test_grid_shape_horizontal() -> None:
    assert grid_shape(4, Direction.HORIZONTAL, 3) == (4, 1)


def test_grid_shape_vertical() -> None:
    assert grid_shape(4, Direction.VERTICAL, 3) == (1, 4)


def test_grid_shape_grid() -> None:
    assert grid_shape(7, Direction.GRID, 3) == (3, 3)
    assert grid_shape(2, Direction.GRID, 5) == (2, 1)


def test_grid_shape_stack() -> None:
    assert grid_shape(4, Direction.STACK, 3) == (1, 4)


def test_grid_shape_rejects_empty() -> None:
    with pytest.raises(ValueError):
        grid_shape(0, Direction.GRID, 3)


def test_positions_horizontal_with_gap_and_margin() -> None:
    spec = LayoutSpec(direction=Direction.HORIZONTAL, gap=2, margin=1)
    assert tile_positions(3, 10, 20, spec) == [(1, 1), (13, 1), (25, 1)]


def test_positions_grid_wraps() -> None:
    spec = LayoutSpec(direction=Direction.GRID, columns=2, gap=0, margin=0)
    assert tile_positions(3, 10, 20, spec) == [(0, 0), (10, 0), (0, 20)]


def test_canvas_size_horizontal() -> None:
    spec = LayoutSpec(direction=Direction.HORIZONTAL, gap=2, margin=1)
    assert canvas_size(3, 10, 20, spec) == (1 * 2 + 3 * 10 + 2 * 2, 2 + 20)


def test_stack_positions() -> None:
    spec = LayoutSpec(direction=Direction.STACK, strip=40, margin=5)
    assert tile_positions(3, 100, 200, spec) == [(5, 5), (5, 45), (5, 85)]


def test_stack_canvas_size() -> None:
    spec = LayoutSpec(direction=Direction.STACK, strip=40, margin=5)
    assert canvas_size(3, 100, 200, spec) == (110, 10 + 200 + 2 * 40)


def test_stack_shows_first_tile_full_and_bottom_strips(tmp_path: Path) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    Image.new("RGB", (10, 10), (255, 0, 0)).save(first)
    Image.new("RGB", (10, 10), (0, 0, 255)).save(second)

    spec = LayoutSpec(direction=Direction.STACK, strip=4)
    sheet = compose([first, second], spec)

    assert sheet.size == (10, 14)
    assert sheet.getpixel((5, 0)) == (255, 0, 0)
    assert sheet.getpixel((5, 9)) == (255, 0, 0)
    assert sheet.getpixel((5, 10)) == (0, 0, 255)
    assert sheet.getpixel((5, 13)) == (0, 0, 255)


def test_compose_layout_and_background(tmp_path: Path) -> None:
    red = tmp_path / "red.png"
    blue = tmp_path / "blue.png"
    Image.new("RGB", (10, 10), (255, 0, 0)).save(red)
    Image.new("RGB", (10, 10), (0, 0, 255)).save(blue)

    spec = LayoutSpec(
        direction=Direction.HORIZONTAL,
        gap=2,
        margin=1,
        background=(0, 255, 0),
    )
    sheet = compose([red, blue], spec)

    assert sheet.size == (24, 12)
    assert sheet.getpixel((0, 0)) == (0, 255, 0)
    assert sheet.getpixel((1, 1)) == (255, 0, 0)
    assert sheet.getpixel((11, 1)) == (0, 255, 0)
    assert sheet.getpixel((13, 1)) == (0, 0, 255)


def test_compose_labels_do_not_crash(tmp_path: Path) -> None:
    tile = tmp_path / "tile.png"
    Image.new("RGB", (40, 30), (10, 10, 10)).save(tile)
    spec = LayoutSpec(show_labels=True)
    sheet = compose([tile], spec, labels=["00:00:01.000"])
    assert sheet.size == (40, 30)
