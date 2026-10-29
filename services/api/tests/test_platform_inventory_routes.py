"""platform-inventory 路由测试（离线：flux 客户端与 JCC 采集器全部 mock）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules import jcc_inventory_collector as collector
from app.routes import platform_inventory as routes

client = TestClient(app)


FLUX_FIXTURE = {
    "observedAt": "2026-10-18T09:00:00+00:00",
    "templates": [
        {
            "id": "2043611007682150400",
            "templateName": "flux任务流程",
            "taskType": "4",
            "labId": "2021093797322031105",
            "labName": None,
            "experimentDesc": "",
            "estimateDays": 15,
            "priority": "medium",
        }
    ],
    "templateParams": {
        "2043611007682150400": [
            {
                "paramName": "recommend_recipes_str",
                "displayName": "方案列表",
                "dataType": "String",
                "isRequired": "1",
                "paramOrder": 1,
                "valueSource": "User",
                "jsonTemplate": None,
                "defaultValue": "[{}]",
                "children": [],
            }
        ]
    },
    "pvdSubRecipes": [
        {
            "id": 1,
            "subRecipeTypeId": 2,
            "subRecipeId": "2",
            "subRecipeName": "LL Auto Pump.dat",
            "groupName": "Auto Pump",
            "enabled": "1",
            "sortOrder": 10,
            "remark": None,
        }
    ],
    "partialErrors": [],
}


def _raw_inventory_fixture() -> dict:
    return {
        "observedAt": "2026-10-18T10:00:00+00:00",
        "queries": {
            "trainGPU": {
                "exitCode": 0,
                "payload": {
                    "data": {
                        "resource": [
                            {
                                "id": 620,
                                "name": "GPU:1*A100",
                                "type": "GPU",
                                "clusterId": 1865927992266461184,
                                "region": "shenzhen",
                                "availableCount": 1,
                                "totalCount": 1,
                                "baseResourceSpecs": [
                                    {"name": "CPU", "totalValue": 8, "totalUnit": "core"}
                                ],
                            }
                        ]
                    }
                },
                "error": "",
            },
            "hpcVasp": {
                "exitCode": 0,
                "payload": {"data": {"list": [{"id": 11, "name": "长沙超算ARM集群"}]}},
                "error": "",
            },
            "trainQuota": {
                "exitCode": 0,
                "payload": {"data": {"resourceRanges": [{"type": "ai.train", "cpu": "8-32"}]}},
                "error": "",
            },
            "trainTasks": {
                "exitCode": 0,
                "payload": {"data": {"list": [{"id": "t1", "name": "my-train", "status": "Succeeded"}]}},
                "error": "",
            },
            "images": {
                "exitCode": 0,
                "payload": {"items": [{"id": "i1", "name": "base-cuda"}], "total": 897},
                "error": "",
            },
            "containers": {"exitCode": 1, "payload": None, "error": "boom"},
        },
    }


@pytest.fixture(autouse=True)
def _reset_collector():
    collector.reset_collector_state_for_tests(payload=None, source="seed")
    yield
    collector.reset_collector_state_for_tests(payload=None, source="seed")


def test_flux_inventory_success(monkeypatch):
    monkeypatch.setattr(routes, "fetch_flux_inventory", lambda *a, **kw: FLUX_FIXTURE)
    response = client.get("/api/platform-inventory/flux")
    assert response.status_code == 200
    body = response.json()
    assert body["observedAt"] == FLUX_FIXTURE["observedAt"]
    assert body["templates"][0]["id"] == "2043611007682150400"
    assert body["templateParams"]["2043611007682150400"][0]["paramName"] == "recommend_recipes_str"
    assert body["pvdSubRecipes"][0]["subRecipeName"] == "LL Auto Pump.dat"


def test_flux_inventory_unavailable(monkeypatch):
    def _boom(*a, **kw):
        raise routes.FluxPlatformError("flux platform unreachable")

    monkeypatch.setattr(routes, "fetch_flux_inventory", _boom)
    response = client.get("/api/platform-inventory/flux")
    assert response.status_code == 503
    assert response.json()["detail"] == "flux_inventory_unavailable"


def test_jcc_inventory_returns_seed():
    response = client.get("/api/platform-inventory/jcc")
    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["source"] == "seed"
    assert body["meta"]["observedAt"]
    assert isinstance(body["payload"]["resources"], list)
    assert body["payload"]["resources"]


def test_jcc_refresh_triggers_collection(monkeypatch):
    monkeypatch.setattr(routes, "collector_available", lambda: {"cliPresent": True, "credentialsPresent": True})
    monkeypatch.setattr(routes, "start_collection_in_background", lambda: True)
    response = client.post("/api/platform-inventory/jcc/refresh")
    assert response.status_code == 202
    body = response.json()
    assert body["collecting"] is True
    assert body["started"] is True


def test_jcc_refresh_without_cli(monkeypatch):
    monkeypatch.setattr(routes, "collector_available", lambda: {"cliPresent": False, "credentialsPresent": True})
    response = client.post("/api/platform-inventory/jcc/refresh")
    assert response.status_code == 503
    assert response.json()["detail"] == "jcc_collector_unavailable"


def test_jcc_collection_updates_cache(monkeypatch):
    monkeypatch.setattr(collector, "collect_raw_inventory", _raw_inventory_fixture)
    collector._run_collection()
    response = client.get("/api/platform-inventory/jcc")
    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["source"] == "cache"
    assert body["meta"]["observedAt"] == "2026-10-18T10:00:00+00:00"
    payload = body["payload"]
    assert payload["resources"][0]["id"] == "train-620"
    assert payload["resources"][0]["details"] == [{"name": "CPU", "totalValue": 8, "totalUnit": "core"}]
    hpc_rows = [r for r in payload["resources"] if r["group"] == "hpc"]
    assert hpc_rows and hpc_rows[0]["name"] == "长沙超算ARM集群"
    assert payload["tasks"][0]["name"] == "my-train"
    assert payload["images"][0]["name"] == "base-cuda"
    assert payload["imageTotal"] == 897
    # 容器查询失败应记入 partialErrors 而不是炸整轮
    assert any("containers" in e for e in payload["partialErrors"])


def test_normalize_inventory_missing_sections_soft_fail():
    normalized = collector.normalize_inventory({"observedAt": "x", "queries": {}})
    assert normalized["resources"] == []
    assert normalized["tasks"] == []
    assert normalized["images"] == []
    assert normalized["imageTotal"] is None
    assert normalized["observedAt"] == "x"


def test_stale_refresh_backoff_after_failure(monkeypatch):
    """上一轮采集失败后，过期 seed 的自动补采在退避窗口内不再重试。"""
    monkeypatch.setenv("JCC_INVENTORY_TTL_SECONDS", "1")
    stale_payload = {"observedAt": "2026-10-14T00:00:00+00:00", "resources": [], "tasks": []}
    collector.reset_collector_state_for_tests(payload=stale_payload, source="seed")
    monkeypatch.setattr(
        collector, "collector_available",
        lambda: {"cliPresent": True, "credentialsPresent": True},
    )
    started: list[bool] = []
    monkeypatch.setattr(
        collector, "start_collection_in_background", lambda: started.append(True) or True,
    )
    # 无失败记录：正常触发
    assert collector.maybe_refresh_if_stale() is True
    assert len(started) == 1
    # 模拟刚失败的一轮（lastError + 片刻前的 lastAttemptAt）：退避窗口内不触发
    collector._state.lastError = "all JCC queries failed"
    collector._state.lastAttemptAt = datetime.now(timezone.utc).isoformat()
    assert collector.maybe_refresh_if_stale() is False
    assert len(started) == 1
    # 退避窗口外：恢复触发
    collector._state.lastAttemptAt = (
        datetime.now(timezone.utc) - timedelta(seconds=collector._FAILURE_BACKOFF_SECONDS + 60)
    ).isoformat()
    assert collector.maybe_refresh_if_stale() is True
    assert len(started) == 2
