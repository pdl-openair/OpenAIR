"""Extract declared metric values from an already located table row."""

from __future__ import annotations

from typing import Any

from .delimited_table_parser import locate_delimited_table
from .metric_values import coerce_text_value
from .parser_errors import ParseFailure
from .pipe_table_parser import locate_pipe_table


def extract_table_metrics(
    text: str,
    fmt: str,
    table: dict[str, Any],
    metrics: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    columns = [metric["selector"]["column"] for metric in metrics]
    if fmt == "pipe_table":
        header, row, table_trace = locate_pipe_table(text, table, columns)
    elif fmt == "csv":
        header, row, table_trace = locate_delimited_table(text, table, columns, ",")
    elif fmt == "tsv":
        header, row, table_trace = locate_delimited_table(text, table, columns, "\t")
    else:
        raise ParseFailure("source_format_invalid")

    results: list[dict[str, Any]] = []
    for metric in metrics:
        column = metric["selector"]["column"]
        positions = [index for index, value in enumerate(header) if value == column]
        if not positions:
            results.append({"metric": metric, "failure_code": "metric_not_found"})
            continue
        if len(positions) != 1:
            results.append({"metric": metric, "failure_code": "metric_ambiguous"})
            continue
        column_index = positions[0]
        raw = row[column_index]
        value, error = coerce_text_value(raw, metric["value_type"])
        if error:
            results.append({"metric": metric, "failure_code": error})
            continue
        results.append({
            "metric": metric,
            "actual_value": value,
            "source_trace": {
                **table_trace,
                "column": column,
                "column_index": column_index,
                "raw_value": raw,
            },
        })
    return results
