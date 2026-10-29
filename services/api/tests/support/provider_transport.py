"""Minimal deterministic provider transport doubles."""

from __future__ import annotations


class Response:
    def __init__(self, body: dict) -> None:
        self.body = body

    def json(self) -> dict:
        return self.body

    def raise_for_status(self) -> None:
        return None


class RecordingTransport:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[dict] = []

    def post(self, endpoint: str, *, headers: dict, json: dict, timeout: float) -> object:
        self.calls.append(
            {"endpoint": endpoint, "headers": headers, "json": json, "timeout": timeout}
        )
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result
