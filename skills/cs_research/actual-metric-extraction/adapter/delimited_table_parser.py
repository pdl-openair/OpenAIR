"""Strict locator for a declared CSV or TSV result table."""

from __future__ import annotations

import csv
import io
from typing import Any

from .parser_errors import ParseFailure


def _parse_rows(text: str, delimiter: str) -> list[list[str]]:
    try:
        return list(csv.reader(io.StringIO(text), delimiter=delimiter, strict=True))
    except (csv.Error, UnicodeError):
        raise ParseFailure("source_format_invalid")


def _first_nonempty(rows: list[list[str]]) -> int | None:
    for index, row in enumerate(rows):
        if row and any(cell != "" for cell in row):
            return index
    return None


def _columns_unique(header: list[str], columns: list[str]) -> bool:
    return all(sum(1 for cell in header if cell == column) == 1 for column in columns)


def _choose_row(rows: list[tuple[int, list[str]]], row_index: int | None) -> tuple[int, list[str], int]:
    if row_index is not None:
        if row_index >= len(rows):
            raise ParseFailure("table_row_not_found")
        source_index, row = rows[row_index]
        return source_index, row, row_index
    if not rows:
        raise ParseFailure("table_row_not_found")
    if len(rows) != 1:
        raise ParseFailure("table_row_ambiguous")
    source_index, row = rows[0]
    return source_index, row, 0


def locate_delimited_table(
    text: str,
    table: dict[str, Any],
    requested_columns: list[str],
    delimiter: str,
) -> tuple[list[str], list[str], dict[str, Any]]:
    rows = _parse_rows(text, delimiter)
    if not rows:
        raise ParseFailure("table_not_found")

    explicit_header = table.get("header")
    if explicit_header is not None:
        exact = [idx for idx, row in enumerate(rows) if row == explicit_header]
        if not exact:
            raise ParseFailure("table_not_found")
        if len(exact) != 1:
            raise ParseFailure("table_ambiguous")
        header_index = exact[0]
        header = rows[header_index]
        table_selection = "explicit_header"
    else:
        header_index = _first_nonempty(rows)
        if header_index is None:
            raise ParseFailure("table_not_found")
        header = rows[header_index]
        if not _columns_unique(header, requested_columns):
            raise ParseFailure("table_not_found")
        table_selection = "declared_structured_table_header"

    data_rows = [
        (idx, row)
        for idx, row in enumerate(rows[header_index + 1 :], start=header_index + 1)
        if row and any(cell != "" for cell in row)
    ]
    if any(len(row) != len(header) for _, row in data_rows):
        raise ParseFailure("source_format_invalid")
    value_index, row, resolved_row_index = _choose_row(data_rows, table.get("row_index"))
    return header, row, {
        "header": list(header),
        "header_row_index": header_index,
        "value_row_index": value_index,
        "row_index": resolved_row_index,
        "table_selection": table_selection,
        "row_selection": "explicit_index" if "row_index" in table else "single_data_row",
    }
