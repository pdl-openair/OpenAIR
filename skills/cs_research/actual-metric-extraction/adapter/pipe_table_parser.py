"""Strict locator for pipe-delimited tables embedded in execution text."""

from __future__ import annotations

import re
from typing import Any

from .parser_errors import ParseFailure

_PIPE_SEPARATOR = re.compile(r":?-{3,}:?\Z")


def _cells(line: str) -> list[str] | None:
    stripped = line.strip()
    if not (stripped.startswith("|") and stripped.endswith("|")):
        return None
    return [cell.strip() for cell in stripped[1:-1].split("|")]


def _is_separator(cells: list[str]) -> bool:
    return bool(cells) and all(_PIPE_SEPARATOR.fullmatch(cell) is not None for cell in cells)


def _column_unique(header: list[str], columns: list[str]) -> bool:
    return all(sum(1 for cell in header if cell == column) == 1 for column in columns)


def _header_matches(
    lines: list[str],
    columns: list[str],
    explicit_header: list[str] | None,
) -> list[tuple[int, list[str]]]:
    matches: list[tuple[int, list[str]]] = []
    for index, line in enumerate(lines):
        row = _cells(line)
        if row is None or _is_separator(row):
            continue
        if explicit_header is not None:
            if row == explicit_header:
                matches.append((index, row))
        elif _column_unique(row, columns):
            matches.append((index, row))
    return matches


def _data_rows(lines: list[str], header_index: int, header: list[str]) -> list[tuple[int, list[str]]]:
    rows: list[tuple[int, list[str]]] = []
    for index in range(header_index + 1, len(lines)):
        row = _cells(lines[index])
        if row is None:
            if rows:
                break
            continue
        if len(row) != len(header):
            if rows:
                break
            continue
        if _is_separator(row):
            continue
        rows.append((index, row))
    return rows


def _choose_row(rows: list[tuple[int, list[str]]], row_index: int | None) -> tuple[int, list[str], int]:
    if row_index is not None:
        if row_index >= len(rows):
            raise ParseFailure("table_row_not_found")
        line_index, row = rows[row_index]
        return line_index, row, row_index
    if not rows:
        raise ParseFailure("table_row_not_found")
    if len(rows) != 1:
        raise ParseFailure("table_row_ambiguous")
    line_index, row = rows[0]
    return line_index, row, 0


def locate_pipe_table(
    text: str,
    table: dict[str, Any],
    requested_columns: list[str],
) -> tuple[list[str], list[str], dict[str, Any]]:
    lines = text.splitlines()
    explicit_header = table.get("header")
    matches = _header_matches(lines, requested_columns, explicit_header)
    if not matches:
        raise ParseFailure("table_not_found")
    if len(matches) != 1:
        raise ParseFailure("table_ambiguous")

    header_index, header = matches[0]
    value_line, row, resolved_row_index = _choose_row(
        _data_rows(lines, header_index, header),
        table.get("row_index"),
    )
    return header, row, {
        "header": list(header),
        "header_line": header_index + 1,
        "value_line": value_line + 1,
        "row_index": resolved_row_index,
        "table_selection": "explicit_header" if explicit_header is not None else "requested_columns_unique_table",
        "row_selection": "explicit_index" if "row_index" in table else "single_data_row",
    }
