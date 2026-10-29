from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_zeabur_api_application_imports_and_generates_openapi() -> None:
    from app.main import app

    document = app.openapi()
    assert document["openapi"]
    assert document["paths"]


def test_zeabur_api_container_runs_api_and_durable_worker() -> None:
    launcher_path = ROOT / "scripts" / "start_zeabur_api.sh"
    assert launcher_path.is_file(), "Zeabur API launcher is missing"

    launcher = launcher_path.read_text(encoding="utf-8")
    assert 'ROLE="${OPENAIR_SERVICE_ROLE:-api}"' in launcher
    assert 'if [ "$ROLE" = "durable-worker" ]; then' in launcher
    assert "exec python -m worker.entrypoint" in launcher
    assert 'if [ "$ROLE" != "api" ]; then' in launcher
    assert "exec uvicorn app.main:app --host 0.0.0.0 --port \"$PORT\"" in launcher

    dockerfile = (ROOT / "Dockerfile.api").read_text(encoding="utf-8")
    assert "COPY scripts/start_zeabur_api.sh /usr/local/bin/start_zeabur_api.sh" in dockerfile
    assert "COPY scripts/openair_healthcheck.sh /usr/local/bin/openair_healthcheck.sh" in dockerfile
    assert "RUN chmod +x /usr/local/bin/start_zeabur_api.sh /usr/local/bin/openair_healthcheck.sh" in dockerfile
    assert 'CMD ["/usr/local/bin/start_zeabur_api.sh"]' in dockerfile
    assert 'CMD ["/usr/local/bin/openair_healthcheck.sh"]' in dockerfile
    assert "COPY packages/llm_core/ ./packages/llm_core/" in dockerfile
    assert "pip install --no-cache-dir ./packages/llm_core" in dockerfile
    assert "pip install --no-cache-dir -e ./packages/llm_core" not in dockerfile
    assert "import llm_core" in dockerfile

    healthcheck = (ROOT / "scripts" / "openair_healthcheck.sh").read_text(encoding="utf-8")
    assert 'ROLE="${OPENAIR_SERVICE_ROLE:-api}"' in healthcheck
    assert "worker.entrypoint" in healthcheck
    assert 'curl -fs "http://127.0.0.1:${PORT}/health"' in healthcheck

    web_dockerfile = (ROOT / "Dockerfile.web").read_text(encoding="utf-8")
    assert "VITE_OPENAIR_API_BASE_URL" in web_dockerfile
    assert "pnpm --filter @openair/web build" in web_dockerfile
    assert "deploy/nginx/web.conf" in web_dockerfile
