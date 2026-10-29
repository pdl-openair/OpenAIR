"""Shared deterministic parser failure type."""


class ParseFailure(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code
