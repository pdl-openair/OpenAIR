"""Stage/substage mapping helpers used by registry generation and reports."""

from __future__ import annotations

from dataclasses import dataclass, field

from .scan import SkillEntry
from .stage_model import RESEARCH_STAGE_ORDER


@dataclass(frozen=True)
class SubstageBinding:
    pack: str
    stage: str
    substage: str
    skill_id: str
    skill_path: str


@dataclass
class StageSubstageValidation:
    bindings: list[SubstageBinding] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)


def resolve_entry_packs(entry: SkillEntry) -> list[str] | None:
    """Return explicit pack membership.

    ``None`` means the skill did not declare pack membership and should be
    treated as legacy/generic by callers.
    """
    sy = entry.skill_yaml or {}
    packs = sy.get("packs")
    if isinstance(packs, list):
        resolved = [item for item in packs if isinstance(item, str) and item]
        return resolved or None
    if isinstance(packs, str) and packs.strip():
        return [packs.strip()]
    pack = sy.get("pack")
    if isinstance(pack, str) and pack.strip():
        return [pack.strip()]
    return None


def build_stage_substage_bindings(entries: list[SkillEntry]) -> StageSubstageValidation:
    """Build pack-scoped substage -> stage bindings from L2 orchestration skills.

    A substage is allowed in exactly one stage within a pack. The same substage
    key may exist in different packs only when it resolves to the same stage.
    """
    validation = StageSubstageValidation()
    seen_by_pack_substage: dict[tuple[str, str], SubstageBinding] = {}
    seen_global_substage: dict[str, SubstageBinding] = {}

    for entry in entries:
        sy = entry.skill_yaml or {}
        if sy.get("skill_type") != "orchestration":
            continue
        pack = sy.get("pack")
        stages = sy.get("stages")
        substages = sy.get("substages")
        if not isinstance(pack, str) or not pack:
            continue
        if not isinstance(stages, list) or len(stages) != 1:
            continue
        if not isinstance(substages, list) or len(substages) != 1:
            continue
        stage = stages[0]
        substage = substages[0]
        if stage not in RESEARCH_STAGE_ORDER or not isinstance(substage, str) or not substage:
            continue

        binding = SubstageBinding(
            pack=pack,
            stage=stage,
            substage=substage,
            skill_id=str(sy.get("skill_id") or ""),
            skill_path=str(entry.path).replace("\\", "/"),
        )
        validation.bindings.append(binding)

        pack_key = (pack, substage)
        previous = seen_by_pack_substage.get(pack_key)
        if previous and previous.stage != stage:
            validation.conflicts.append(
                f"{pack}/{substage} maps to both {previous.stage} and {stage}"
            )
        else:
            seen_by_pack_substage[pack_key] = binding

        global_previous = seen_global_substage.get(substage)
        if global_previous and global_previous.stage != stage:
            validation.conflicts.append(
                f"{substage} maps across stages: "
                f"{global_previous.pack}/{global_previous.stage} and {pack}/{stage}"
            )
        else:
            seen_global_substage[substage] = binding

    validation.conflicts = sorted(set(validation.conflicts))
    return validation


def build_substage_stage_index(entries: list[SkillEntry]) -> dict[str, dict[str, str]]:
    """Return ``{pack: {substage: stage}}`` after validating no cross-stage use."""
    validation = build_stage_substage_bindings(entries)
    if validation.conflicts:
        raise ValueError("; ".join(validation.conflicts))

    index: dict[str, dict[str, str]] = {}
    for binding in validation.bindings:
        index.setdefault(binding.pack, {})[binding.substage] = binding.stage
    return index
