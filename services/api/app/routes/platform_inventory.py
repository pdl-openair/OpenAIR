"""资源广场「设备资源 / 算力资源」平台清单路由（只读 + 后台刷新触发）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.auth import RequestIdentity, resolve_request_identity
from app.models.platform_inventory import FluxInventoryOut, JccInventoryOut, JccRefreshOut
from app.modules.flux_platform_client import FluxPlatformError, fetch_flux_inventory
from app.modules.jcc_inventory_collector import (
    collector_available,
    get_jcc_inventory,
    start_collection_in_background,
)

router = APIRouter(prefix="/platform-inventory", tags=["platform-inventory"])


@router.get("/flux", response_model=FluxInventoryOut)
def get_flux_platform_inventory() -> FluxInventoryOut:
    """云实验平台实时清单（模板目录 + 参数 schema + PVD 工艺字典）。"""
    try:
        payload = fetch_flux_inventory()
    except FluxPlatformError as exc:
        raise HTTPException(status_code=503, detail="flux_inventory_unavailable") from exc
    return FluxInventoryOut.model_validate(payload)


@router.get("/jcc", response_model=JccInventoryOut)
def get_jcc_platform_inventory() -> JccInventoryOut:
    """云际计算平台清单：返回缓存/seed；过期时顺带触发后台补采（stale-while-revalidate）。"""
    return JccInventoryOut.model_validate(get_jcc_inventory())


@router.post("/jcc/refresh", response_model=JccRefreshOut, status_code=202)
def refresh_jcc_platform_inventory(
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> JccRefreshOut:
    """触发一轮后台采集（分钟级）；接口立即返回，前端轮询 GET /jcc 的 collecting 状态。"""
    _ = identity
    availability = collector_available()
    if not availability["cliPresent"]:
        raise HTTPException(status_code=503, detail="jcc_collector_unavailable")
    if not availability["credentialsPresent"]:
        raise HTTPException(status_code=503, detail="jcc_credentials_not_configured")
    started = start_collection_in_background()
    return JccRefreshOut(collecting=True, started=started, availability=availability)
