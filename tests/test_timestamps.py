import pytest

from yass.timestamps import (
    format_filename,
    format_timestamp,
    parse_timestamp,
    parse_timestamp_list,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0", 0.0),
        ("12", 12.0),
        ("83.5", 83.5),
        ("01:23", 83.0),
        ("1:23.250", 83.25),
        ("00:01:23.456", 83.456),
        ("1:02:03", 3723.0),
        ("00:00:00.000", 0.0),
        ("1:23,500", 83.5),
        ("  00:10  ", 10.0),
    ],
)
def test_parse_valid(text: str, expected: float) -> None:
    assert parse_timestamp(text) == expected


@pytest.mark.parametrize(
    "text",
    ["", "-1", "1:60", "1:2:60", "abc", "1:2:3:4", "1.5:30", ":", "1:", "::1"],
)
def test_parse_invalid(text: str) -> None:
    with pytest.raises(ValueError):
        parse_timestamp(text)


def test_format_timestamp() -> None:
    assert format_timestamp(83.456) == "00:01:23.456"
    assert format_timestamp(3723.0) == "01:02:03.000"


def test_format_filename() -> None:
    assert format_filename(83.456) == "00-01-23-456"


def test_list_sorts_input() -> None:
    parsed = parse_timestamp_list("0:30\n0:05\n1:00\n")
    assert [entry.seconds for entry in parsed.entries] == [5.0, 30.0, 60.0]
    assert not parsed.errors
    assert not parsed.warnings


def test_list_deduplicates() -> None:
    parsed = parse_timestamp_list("1:00\n60\n00:01:00.000\n")
    assert [entry.seconds for entry in parsed.entries] == [60.0]
    assert len(parsed.warnings) == 2


def test_list_skips_blank_and_comments() -> None:
    parsed = parse_timestamp_list("\n# comment\n5\n\n")
    assert [entry.seconds for entry in parsed.entries] == [5.0]


def test_list_reports_errors_with_line_numbers() -> None:
    parsed = parse_timestamp_list("5\nnot a time\n10\n")
    assert [entry.seconds for entry in parsed.entries] == [5.0, 10.0]
    assert parsed.errors == ["Line 2: invalid timestamp 'not a time'"]


def test_list_marks_out_of_range() -> None:
    parsed = parse_timestamp_list("50\n150\n", duration=100.0)
    assert [(entry.seconds, entry.in_range) for entry in parsed.entries] == [
        (50.0, True),
        (150.0, False),
    ]
    assert len(parsed.warnings) == 1
    assert "beyond the video duration" in parsed.warnings[0]
