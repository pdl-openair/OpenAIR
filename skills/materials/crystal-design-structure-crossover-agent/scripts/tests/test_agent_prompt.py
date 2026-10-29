# scripts/tests/test_agent_prompt.py
from agent_prompt import (
    build_agent_prompt,
    build_agent_prompt_no_memory,
)
from memory_store import MemoryEntry


def _mem(entry_id: str = "x1", context: str = "Au collapse") -> MemoryEntry:
    return MemoryEntry(
        entry_id=entry_id,
        run_id="r1",
        family="rare_earth_intermetallics",
        gen=2,
        context=context,
        action="ban element Au",
        outcome={"delta_sun": 0.25},
        confidence=0.8,
        created_at="2026-08-12T10:00:00+00:00",
    )


def test_prompt_with_memory_includes_section() -> None:
    snapshot = {"generation": 2, "pareto_size": 4, "sun_count": 7}
    constraints = {"num_elements": {"min": 2, "weight": 10}}
    prompt = build_agent_prompt(
        snapshot=snapshot,
        family="rare_earth_intermetallics",
        retrieved=[_mem()],
        constraints=constraints,
    )
    assert "RETRIEVED MEMORIES" in prompt
    assert "Au collapse" in prompt
    assert "ban element Au" in prompt
    assert "STEER ACTION" in prompt  # output contract


def test_prompt_no_memory_omits_section() -> None:
    snapshot = {"generation": 2}
    prompt = build_agent_prompt_no_memory(
        snapshot=snapshot,
        family="rare_earth_intermetallics",
        constraints={},
    )
    assert "RETRIEVED MEMORIES" not in prompt
    assert "STEER ACTION" in prompt


def test_prompt_is_valid_json_contract() -> None:
    """Prompt must instruct agent to emit a JSON steer action."""
    prompt = build_agent_prompt_no_memory(
        snapshot={"generation": 1},
        family="x",
        constraints={},
    )
    assert '"action"' in prompt
    assert '"args"' in prompt
