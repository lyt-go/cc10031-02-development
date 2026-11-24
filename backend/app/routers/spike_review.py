"""尖峰分段复验专用路由：窗口/基线/隔离点/待复核尖峰/对照视图。

与通用资源路由并列注册；路径前缀固定为 /api/spike-review，不会与
/api/{module} 动态路由冲突（FastAPI 按注册顺序匹配，本路由先注册）。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.spike_review import SpikeReviewService
from app.services.spike_review_scenario import build_scenario

router = APIRouter(prefix="/api/spike-review", tags=["尖峰分段复验"])

service = SpikeReviewService()
_scenario_ready = False


def _ensure_ok(result: dict[str, Any]) -> dict[str, Any]:
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


class WindowPayload(BaseModel):
    point_key: str
    segment: str
    start_ts: str
    end_ts: str
    threshold_multiplier: float = 0.5
    reviewer: str = ""
    note: str = ""


class ObservationItem(BaseModel):
    timestamp: str
    value: float


class ObservationsPayload(BaseModel):
    window_key: str
    observations: list[ObservationItem]
    source: str = "original"


class BaselinePayload(BaseModel):
    window_key: str
    reviewer: str
    basis_note: str = ""
    multiplier: float | None = None


class BaselineApprovalPayload(BaseModel):
    window_key: str
    baseline_id: str
    approver: str


class BaselineRejectPayload(BaseModel):
    window_key: str
    baseline_id: str
    reason: str = ""


class SubmitReviewPayload(BaseModel):
    window_key: str
    baseline_id: str | None = None


class ConfirmPayload(BaseModel):
    window_key: str
    approver: str
    note: str = ""


class EquipmentEventPayload(BaseModel):
    window_key: str
    kind: str
    detail: dict[str, Any] = Field(default_factory=dict)


@router.post("/scenario/init")
def init_standard_scenario() -> dict[str, Any]:
    """装载（幂等重置）升速前后分段复验标准业务场景。"""
    global _scenario_ready
    service.__init__()  # 重置内存状态与流水号
    result = build_scenario(service)
    _scenario_ready = True
    return {"ok": True, "message": "标准分段复验场景已就绪", **result}


@router.get("/windows")
def list_windows(point_key: str | None = None) -> dict[str, Any]:
    windows = service.list_windows(point_key)
    return {"items": windows, "total": len(windows)}


@router.post("/windows")
def register_window(payload: WindowPayload) -> dict[str, Any]:
    return _ensure_ok(service.register_window(
        payload.point_key, payload.segment, payload.start_ts, payload.end_ts,
        threshold_multiplier=payload.threshold_multiplier,
        reviewer=payload.reviewer, note=payload.note,
    ))


@router.post("/observations")
def load_observations(payload: ObservationsPayload) -> dict[str, Any]:
    return _ensure_ok(service.load_observations(
        payload.window_key,
        [o.model_dump() for o in payload.observations],
        source=payload.source,
    ))


@router.post("/equipment-events")
def add_equipment_event(payload: EquipmentEventPayload) -> dict[str, Any]:
    return _ensure_ok(service.add_equipment_event(
        payload.window_key, payload.kind, payload.detail))


@router.post("/baselines/compute")
def compute_baseline(payload: BaselinePayload) -> dict[str, Any]:
    return _ensure_ok(service.compute_baseline(
        payload.window_key, reviewer=payload.reviewer,
        basis_note=payload.basis_note, multiplier=payload.multiplier,
    ))


@router.post("/baselines/approve")
def approve_baseline(payload: BaselineApprovalPayload) -> dict[str, Any]:
    return _ensure_ok(service.approve_baseline(
        payload.window_key, payload.baseline_id, approver=payload.approver))


@router.post("/baselines/reject")
def reject_baseline(payload: BaselineRejectPayload) -> dict[str, Any]:
    return _ensure_ok(service.reject_baseline(
        payload.window_key, payload.baseline_id, reason=payload.reason))


@router.post("/reviews/submit")
def submit_review(payload: SubmitReviewPayload) -> dict[str, Any]:
    return _ensure_ok(service.submit_review(
        payload.window_key, baseline_id=payload.baseline_id))


@router.post("/windows/confirm")
def confirm_window(payload: ConfirmPayload) -> dict[str, Any]:
    return _ensure_ok(service.confirm_window(
        payload.window_key, approver=payload.approver, note=payload.note))


@router.get("/comparison/{window_key}")
def comparison_view(window_key: str) -> dict[str, Any]:
    try:
        return service.comparison_view(window_key)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
