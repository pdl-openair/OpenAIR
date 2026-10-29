"""资源广场「设备资源 / 算力资源」平台清单响应模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


# ---------------------------------------------------------------- flux（云实验平台）


class FluxTemplate(BaseModel):
    id: str
    templateName: str | None = None
    taskType: str | int | None = None
    labId: str | None = None
    labName: str | None = None
    experimentDesc: str | None = None
    estimateDays: int | None = None
    priority: str | None = None


class FluxTemplateParam(BaseModel):
    paramName: str | None = None
    displayName: str | None = None
    dataType: str | None = None
    isRequired: str | None = None
    paramOrder: int | None = None
    valueSource: str | None = None
    jsonTemplate: str | None = None
    defaultValue: str | None = None
    children: list[FluxTemplateParam] = []


class FluxSubRecipe(BaseModel):
    id: int | str | None = None
    subRecipeTypeId: int | str | None = None
    subRecipeId: str | int | None = None
    subRecipeName: str | None = None
    groupName: str | None = None
    enabled: str | None = None
    sortOrder: int | None = None
    remark: str | None = None


class FluxInventoryOut(BaseModel):
    observedAt: str
    templates: list[FluxTemplate]
    templateParams: dict[str, list[FluxTemplateParam]] = {}
    pvdSubRecipes: list[FluxSubRecipe] = []
    partialErrors: list[str] = []


# ---------------------------------------------------------------- jcc（云际计算平台）


class JccResourceDetail(BaseModel):
    name: str | None = None
    totalValue: int | float | None = None
    totalUnit: str | None = None


class JccResourceRow(BaseModel):
    id: str
    specId: str = ""
    group: str
    name: str | None = None
    type: str | None = None
    clusterId: str = ""
    region: str | None = None
    availableCount: int | None = None
    totalCount: int | None = None
    details: list[JccResourceDetail] = []


class JccQuotaRange(BaseModel):
    type: str | None = None
    # 平台配额字段有 "8-32" 字符串与 {max, min} 对象两种形态，原样透传
    cpu: Any = None
    gpuNumber: Any = None
    memory: Any = None
    storage: Any = None


class JccTaskRow(BaseModel):
    id: str | int | None = None
    name: str | None = None
    status: str | None = None
    createdTime: str | None = None
    startTime: str | None = None
    endTime: str | None = None


class JccImageRow(BaseModel):
    id: str | int | None = None
    name: str | None = None
    arch: str | None = None
    computeType: str | None = None
    workloadKind: str | None = None
    availabilityStatus: str | None = None


class JccInventoryPayload(BaseModel):
    observedAt: str | None = None
    resources: list[JccResourceRow] = []
    trainQuota: list[JccQuotaRange] = []
    inferQuota: list[JccQuotaRange] = []
    tasks: list[JccTaskRow] = []
    images: list[JccImageRow] = []
    imageTotal: int | None = None
    partialErrors: list[str] = []


class JccInventoryMeta(BaseModel):
    source: str
    observedAt: str | None = None
    collecting: bool = False
    lastAttemptAt: str | None = None
    lastError: str | None = None


class JccInventoryOut(BaseModel):
    meta: JccInventoryMeta
    payload: JccInventoryPayload | None = None


class JccRefreshOut(BaseModel):
    collecting: bool
    started: bool
    availability: dict[str, Any] = {}
