from __future__ import annotations

import time
from pathlib import Path

from app.execution.python_adapter_runner import _run_registered_adapter_entrypoint


def test_registered_adapter_progress_arrives_before_final_result(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skill"
    adapter_dir = skill_dir / "adapter"
    adapter_dir.mkdir(parents=True)
    (adapter_dir / "__init__.py").write_text("", encoding="utf-8")
    (adapter_dir / "demo.py").write_text(
        '''from __future__ import annotations\nimport json\nimport os\nimport time\nfrom pathlib import Path\n\ndef run(_payload):\n    p = Path(os.environ["OPENAIR_SKILL_PROGRESS_FILE"])\n    with p.open("a", encoding="utf-8") as h:\n        h.write(json.dumps({"phase": "source_batch_completed", "source_name": "openalex"}) + "\\n")\n        h.flush()\n    time.sleep(0.30)\n    return {"ok": True}\n''',
        encoding="utf-8",
    )

    callback_times: list[float] = []
    started = time.perf_counter()
    result = _run_registered_adapter_entrypoint(
        repo_root=Path(__file__).resolve().parents[2],
        skill_dir=skill_dir,
        entrypoint=("adapter.demo", "run", "payload_v1"),
        input_payload={},
        progress_callback=lambda _event: callback_times.append(time.perf_counter() - started),
    )
    elapsed = time.perf_counter() - started

    assert result == {"ok": True}
    assert len(callback_times) == 1
    assert callback_times[0] < elapsed - 0.10
