"""Original method role, now capable of preparing its own valid inputs."""

from .repair_entry import run_reproduction_execution_input_repair


def run_reproduction_execution_input_closure(payload):
    return run_reproduction_execution_input_repair(payload)
