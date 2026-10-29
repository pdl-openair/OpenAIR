from __future__ import annotations

from pathlib import Path

from app.modules import postgres_migrations, runtime_source_version
from app.modules.repo_paths import api_service_root, backend_app_root, repo_root


def test_postgres_migrations_uses_canonical_repo_root() -> None:
    expected = api_service_root() / "migrations"
    assert postgres_migrations.MIGRATIONS_DIR == expected
    assert postgres_migrations.MIGRATIONS_DIR.is_dir()


def test_runtime_source_version_covers_canonical_app_tree(
    monkeypatch,
) -> None:
    observed: list[Path] = []
    real_rglob = Path.rglob

    def tracking_rglob(self: Path, pattern: str):
        observed.append(self)
        return real_rglob(self, pattern)

    monkeypatch.setattr(Path, "rglob", tracking_rglob)
    runtime_source_version.runtime_source_version.cache_clear()
    value = runtime_source_version.runtime_source_version()
    runtime_source_version.runtime_source_version.cache_clear()

    assert len(value) == 64
    assert observed
    assert observed[0] == backend_app_root()


def test_production_modules_do_not_use_fixed_parent_depth() -> None:
    modules = repo_root() / "services" / "api" / "app"
    offenders: list[str] = []
    for path in modules.rglob("*.py"):
        relative = path.relative_to(modules)
        if (
            path.name.startswith("test_")
            or any(part.startswith("test_") or part == "tests" for part in relative.parts)
        ):
            continue
        text = path.read_text(encoding="utf-8")
        if (
            "Path(__file__).resolve().parents[" in text
            or "Path(__file__).parents[" in text
        ):
            offenders.append(relative.as_posix())
    assert offenders == []


def test_backend_path_authority_supports_flattened_service_layout(
    tmp_path,
    monkeypatch,
) -> None:
    import app as app_package

    app_root = tmp_path / "app"
    modules_dir = app_root / "modules"
    modules_dir.mkdir(parents=True)
    (app_root / "__init__.py").write_text("", encoding="utf-8")
    (modules_dir / "runtime_probe.py").write_text(
        "VALUE = 'same-backend-source'\n",
        encoding="utf-8",
    )
    (tmp_path / "migrations").mkdir()

    monkeypatch.setattr(app_package, "__file__", str(app_root / "__init__.py"))

    runtime_source_version.runtime_source_version.cache_clear()
    value = runtime_source_version.runtime_source_version()
    runtime_source_version.runtime_source_version.cache_clear()

    assert len(value) == 64
    assert backend_app_root() == app_root.resolve()
    assert api_service_root() == tmp_path.resolve()


def test_python_adapter_runner_uses_backend_package_path_authority() -> None:
    runner = repo_root() / "services" / "api" / "app" / "execution" / "python_adapter_runner.py"
    source = runner.read_text(encoding="utf-8")

    assert 'modules_dir = backend_app_root() / \\"modules\\"' in source
    assert 'repo_root / \\"services\\" / \\"api\\" / \\"app\\" / \\"modules\\"' not in source
