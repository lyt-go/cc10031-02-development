"""尖峰分段复验领域服务。

业务背景
========
测点在设备升速前后可能各出现一段短暂高值。旧班可能已确认其中一段为生产数据；
随后迟到的平稳观测会拉低另一段（未确认窗口）的均值基线。本服务保证：

1. 复验以「窗口」为单位，每个窗口有独立、可复现的基线计算快照；
2. 已确认窗口的基线一经获准即冻结，后到数据只能登记为隔离点，绝不参与
   原窗口的阈值计算（不能把后到数据混作原基线）；
3. 同一窗口的一次正式复验只允许采用一条获准基线，两份前段依据都可查，
   但不能在同一次复验里混合两个基线；
4. 对照视图逐段给出：实际参与阈值计算的连续观测、被隔离的点、启停记录，
   以及该步送往待复核的尖峰数。

数据全部落盘到 ``DATA_ROOT/spike_review``，保证计算可复现、依据可审计。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from app.services.base import BaseModuleService

# 窗口状态机
WINDOW_DRAFT = "草稿"
WINDOW_REVIEWING = "复验中"        # 已发起复验，至少存在一版计算快照
WINDOW_PENDING = "待复核"          # 已选定获准基线，尖峰已送往待复核
WINDOW_CONFIRMED = "已确认"        # 旧班/维护人员已确认，基线冻结
WINDOW_STATUSES = [WINDOW_DRAFT, WINDOW_REVIEWING, WINDOW_PENDING, WINDOW_CONFIRMED]

# 基线版本状态机
BASELINE_CALCULATING = "计算中"
BASELINE_PENDING_APPROVAL = "待批准"
BASELINE_APPROVED = "已获准"
BASELINE_REJECTED = "已拒绝"

# 观测来源标记
SOURCE_ORIGINAL = "original"        # 复验发起时已在有效窗内的观测
SOURCE_LATE = "late_arrival"        # 复验发起后才到达的观测
SOURCE_ISOLATED = "isolated"        # 被隔离、不参与任何已冻结基线计算的点


class SpikeReviewService(BaseModuleService):
    """尖峰分段复验：窗口、基线快照、隔离点、待复核尖峰与对照视图。"""

    MODULE = "spike_filter"
    ENTITY = "尖峰复验窗口"

    def __init__(self) -> None:
        # 窗口注册表：window_key -> window dict
        self._windows: dict[str, dict[str, Any]] = {}
        # 观测流水：window_key -> list[observation]
        self._observations: dict[str, list[dict[str, Any]]] = {}
        # 基线版本：window_key -> list[baseline dict]
        self._baselines: dict[str, list[dict[str, Any]]] = {}
        # 启停/设备事件：window_key -> list[event]
        self._events: dict[str, list[dict[str, Any]]] = {}
        # 待复核尖峰：window_key -> list[spike]（仅反映最近一次获准基线的判定）
        self._pending_spikes: dict[str, list[dict[str, Any]]] = {}
        self._seq = 0

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    def _next_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self._seq:04d}"

    def _review_dir(self) -> Path:
        return self._data_dir("spike_review")

    def _window_file(self, window_key: str) -> Path:
        return self._review_dir() / f"{window_key}_window.json"

    def _baseline_file(self, window_key: str) -> Path:
        return self._review_dir() / f"{window_key}_baselines.jsonl"

    def _observation_file(self, window_key: str) -> Path:
        return self._review_dir() / f"{window_key}_observations.jsonl"

    def _event_file(self, window_key: str) -> Path:
        return self._review_dir() / f"{window_key}_events.jsonl"

    def _spike_file(self, window_key: str) -> Path:
        return self._review_dir() / f"{window_key}_pending_spikes.jsonl"

    def _persist_window(self, window: dict[str, Any]) -> None:
        with open(self._window_file(window["window_key"]), "w", encoding="utf-8") as f:
            json.dump(window, f, ensure_ascii=False, indent=2, default=str)

    def _append_jsonl(self, path: Path, record: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def _get_window(self, window_key: str) -> dict[str, Any]:
        window = self._windows.get(window_key)
        if window is None:
            raise KeyError(f"复验窗口 {window_key} 不存在")
        return window

    @staticmethod
    def _value_of(obs: dict[str, Any]) -> float:
        return float(obs["value"])

    def _fingerprint(self, observations: list[dict[str, Any]]) -> str:
        """对参与计算的观测集合生成数据指纹，用于复现校验。"""
        payload = json.dumps(
            [{"ts": o["timestamp"], "v": o["value"], "src": o.get("source", SOURCE_ORIGINAL)}
             for o in observations],
            ensure_ascii=False, sort_keys=True,
        )
        return self._compute_checksum(payload)

    def _log_event(self, window_key: str, kind: str, detail: dict[str, Any]) -> None:
        event = {
            "event_id": self._next_id("EVT"),
            "window_key": window_key,
            "kind": kind,
            "detail": detail,
            "at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            "ts_epoch": time.time(),
        }
        self._events.setdefault(window_key, []).append(event)
        self._append_jsonl(self._event_file(window_key), event)

    # ------------------------------------------------------------------
    # 1. 窗口注册与观测装载
    # ------------------------------------------------------------------
    def register_window(
        self,
        point_key: str,
        segment: str,
        start_ts: str,
        end_ts: str,
        *,
        threshold_multiplier: float = 0.5,
        reviewer: str = "",
        note: str = "",
    ) -> dict[str, Any]:
        """登记一个分段复验窗口（有效窗），同测点同段唯一。"""
        window_key = f"RW-{point_key}-{segment}"
        if window_key in self._windows:
            return {"error": f"窗口 {window_key} 已登记，不能重复创建"}
        window = {
            "window_key": window_key,
            "point_key": point_key,
            "segment": segment,
            "start_ts": start_ts,
            "end_ts": end_ts,
            "threshold_multiplier": float(threshold_multiplier),
            "status": WINDOW_DRAFT,
            "reviewer": reviewer,
            "note": note,
            "frozen_baseline_id": None,
            "active_baseline_id": None,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        }
        self._windows[window_key] = window
        self._observations[window_key] = []
        self._baselines[window_key] = []
        self._events[window_key] = []
        self._pending_spikes[window_key] = []
        self._persist_window(window)
        self._log_event(window_key, "window_registered", {
            "point": point_key, "segment": segment,
            "valid_window": [start_ts, end_ts],
            "threshold_multiplier": threshold_multiplier, "reviewer": reviewer,
        })
        return dict(window)

    def load_observations(
        self,
        window_key: str,
        observations: list[dict[str, Any]],
        *,
        source: str = SOURCE_ORIGINAL,
    ) -> dict[str, Any]:
        """向窗口装载观测。

        - ``original``：复验发起时已存在、落在有效窗内的连续观测；
        - ``late_arrival``：复验发起后才到达的观测。若窗口基线已冻结，
          这类点会被强制隔离，绝不进入原基线。
        """
        window = self._get_window(window_key)
        accepted, isolated = [], []
        frozen = window["status"] == WINDOW_CONFIRMED or window["frozen_baseline_id"]
        for obs in observations:
            record = {
                "timestamp": str(obs["timestamp"]),
                "value": float(obs["value"]),
                "source": source,
                "window_key": window_key,
                "isolated": False,
                "isolation_reason": "",
                "received_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            }
            in_window = window["start_ts"] <= record["timestamp"] <= window["end_ts"]
            if source == SOURCE_LATE and (frozen or not in_window):
                record["isolated"] = True
                record["source"] = SOURCE_ISOLATED
                record["isolation_reason"] = (
                    "窗口基线已冻结，迟到数据隔离，不参与原基线计算"
                    if frozen else "观测落在有效窗之外，隔离"
                )
                isolated.append(record)
            elif not in_window:
                record["isolated"] = True
                record["source"] = SOURCE_ISOLATED
                record["isolation_reason"] = "观测落在有效窗之外，隔离"
                isolated.append(record)
            else:
                accepted.append(record)
            self._observations.setdefault(window_key, []).append(record)
            self._append_jsonl(self._observation_file(window_key), record)
        self._log_event(window_key, "observations_loaded", {
            "source": source, "accepted": len(accepted), "isolated": len(isolated),
            "frozen": bool(frozen),
        })
        return {"window_key": window_key, "accepted": len(accepted),
                "isolated": len(isolated), "frozen": bool(frozen)}

    # ------------------------------------------------------------------
    # 2. 基线计算快照
    # ------------------------------------------------------------------
    def _participating_observations(self, window_key: str) -> list[dict[str, Any]]:
        """返回当前实际参与阈值计算的连续观测（排除全部隔离点）。"""
        return [o for o in self._observations.get(window_key, []) if not o["isolated"]]

    def _isolated_observations(self, window_key: str) -> list[dict[str, Any]]:
        return [o for o in self._observations.get(window_key, []) if o["isolated"]]

    def compute_baseline(
        self,
        window_key: str,
        *,
        reviewer: str,
        basis_note: str = "",
        multiplier: float | None = None,
    ) -> dict[str, Any]:
        """基于当前有效窗内、未隔离的连续观测计算一版基线快照。

        每次调用都生成不可变快照：记录参与计算的观测指纹、均值、阈值、
        隔离点与启停事件，供后续复现与审计。
        """
        window = self._get_window(window_key)
        if window["status"] == WINDOW_CONFIRMED:
            return {"error": f"窗口 {window_key} 已确认，基线已冻结，不能再计算新基线"}
        participating = self._participating_observations(window_key)
        if not participating:
            return {"error": "有效窗内没有可参与计算的连续观测，无法计算基线"}
        mult = float(multiplier if multiplier is not None else window["threshold_multiplier"])
        values = [self._value_of(o) for o in participating]
        mean_val = sum(values) / len(values)
        threshold = abs(mean_val) * mult
        fingerprint = self._fingerprint(participating)
        baseline_id = self._next_id("BASE")
        snapshot = {
            "baseline_id": baseline_id,
            "window_key": window_key,
            "point_key": window["point_key"],
            "segment": window["segment"],
            "reviewer": reviewer,
            "basis_note": basis_note,
            "status": BASELINE_PENDING_APPROVAL,
            "mean": round(mean_val, 6),
            "threshold_multiplier": mult,
            "threshold_delta": round(threshold, 6),
            "valid_window": [window["start_ts"], window["end_ts"]],
            "participating_count": len(participating),
            "participating_fingerprint": fingerprint,
            "participating_observations": [
                {"timestamp": o["timestamp"], "value": o["value"], "source": o["source"]}
                for o in participating
            ],
            "isolated_points": [
                {"timestamp": o["timestamp"], "value": o["value"],
                 "reason": o["isolation_reason"], "source": o["source"]}
                for o in self._isolated_observations(window_key)
            ],
            "start_stop_events": [
                {"event_id": e["event_id"], "kind": e["kind"], "at": e["at"],
                 "detail": e["detail"]}
                for e in self._events.get(window_key, [])
            ],
            "late_points_excluded": sum(
                1 for o in self._isolated_observations(window_key)
                if o["isolation_reason"].startswith("窗口基线已冻结")
            ),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        }
        self._baselines.setdefault(window_key, []).append(snapshot)
        self._append_jsonl(self._baseline_file(window_key), snapshot)
        if window["status"] == WINDOW_DRAFT:
            window["status"] = WINDOW_REVIEWING
        window["active_baseline_id"] = baseline_id
        self._persist_window(window)
        self._log_event(window_key, "baseline_computed", {
            "baseline_id": baseline_id, "reviewer": reviewer,
            "mean": snapshot["mean"], "participating": len(participating),
            "fingerprint": fingerprint, "basis_note": basis_note,
        })
        return dict(snapshot)

    # ------------------------------------------------------------------
    # 3. 基线批准/拒绝：同一窗口只允许一条获准基线
    # ------------------------------------------------------------------
    def approve_baseline(self, window_key: str, baseline_id: str, *, approver: str) -> dict[str, Any]:
        window = self._get_window(window_key)
        if window["frozen_baseline_id"]:
            return {"error": (
                f"窗口 {window_key} 已有获准基线 {window['frozen_baseline_id']}，"
                "一次正式复验不能混合两个基线；如需更正请对未确认窗口另起复验"
            )}
        target = next((b for b in self._baselines.get(window_key, [])
                       if b["baseline_id"] == baseline_id), None)
        if target is None:
            return {"error": f"基线 {baseline_id} 在窗口 {window_key} 中不存在"}
        if target["status"] == BASELINE_APPROVED:
            return {"error": f"基线 {baseline_id} 已获准，请勿重复批准"}
        # 冻结：其它待批准版本一律标记拒绝，保留可查但不得再用于本次复验
        for b in self._baselines[window_key]:
            if b["baseline_id"] == baseline_id:
                b["status"] = BASELINE_APPROVED
                b["approver"] = approver
                b["approved_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
            elif b["status"] == BASELINE_PENDING_APPROVAL:
                b["status"] = BASELINE_REJECTED
                b["reject_reason"] = "同一窗口已有另一条基线获准，本版本不得混用"
        # 重写基线文件以反映状态变更（追加一条状态事件，原快照仍在文件中可查）
        self._append_jsonl(self._baseline_file(window_key), {
            "baseline_id": baseline_id, "window_key": window_key,
            "status": BASELINE_APPROVED, "approver": approver,
            "at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            "event": "approval_state_change",
        })
        window["frozen_baseline_id"] = baseline_id
        window["active_baseline_id"] = baseline_id
        self._persist_window(window)
        self._log_event(window_key, "baseline_approved", {
            "baseline_id": baseline_id, "approver": approver,
        })
        return {"window_key": window_key, "approved_baseline_id": baseline_id,
                "approver": approver}

    def reject_baseline(self, window_key: str, baseline_id: str, *, reason: str = "") -> dict[str, Any]:
        target = next((b for b in self._baselines.get(window_key, [])
                       if b["baseline_id"] == baseline_id), None)
        if target is None:
            return {"error": f"基线 {baseline_id} 在窗口 {window_key} 中不存在"}
        if target["status"] == BASELINE_APPROVED:
            return {"error": "已获准基线不能拒绝，它是该窗口的正式复验依据"}
        target["status"] = BASELINE_REJECTED
        target["reject_reason"] = reason
        self._append_jsonl(self._baseline_file(window_key), {
            "baseline_id": baseline_id, "window_key": window_key,
            "status": BASELINE_REJECTED, "reason": reason,
            "at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            "event": "approval_state_change",
        })
        self._log_event(window_key, "baseline_rejected", {
            "baseline_id": baseline_id, "reason": reason,
        })
        return {"window_key": window_key, "rejected_baseline_id": baseline_id}

    # ------------------------------------------------------------------
    # 4. 正式复验：用获准基线重判尖峰，送往待复核
    # ------------------------------------------------------------------
    def submit_review(self, window_key: str, *, baseline_id: str | None = None) -> dict[str, Any]:
        """用指定（或窗口已获准）基线完成正式复验，输出待复核尖峰。

        一次正式复验对同一窗口只能采用一条基线；若传入的基线与已获准基线
        不一致，直接拒绝，杜绝两个基线混用。
        """
        window = self._get_window(window_key)
        chosen_id = baseline_id or window["frozen_baseline_id"]
        if not chosen_id:
            return {"error": "窗口尚无获准基线，不能提交正式复验"}
        if window["frozen_baseline_id"] and chosen_id != window["frozen_baseline_id"]:
            return {"error": (
                f"本次复验指定基线 {chosen_id}，但窗口正式获准基线为 "
                f"{window['frozen_baseline_id']}，同一窗口不能混合两个基线"
            )}
        baseline = next((b for b in self._baselines[window_key]
                         if b["baseline_id"] == chosen_id
                         and b["status"] == BASELINE_APPROVED), None)
        if baseline is None:
            return {"error": f"基线 {chosen_id} 未获准，不能作为正式复验依据"}
        # 复现校验：当前参与计算的观测指纹必须与快照一致
        current_fp = self._fingerprint(self._participating_observations(window_key))
        if current_fp != baseline["participating_fingerprint"]:
            return {"error": (
                "参与计算观测与基线快照指纹不一致，基线不可复现；"
                f"快照={baseline['participating_fingerprint']} 当前={current_fp}"
            )}
        mean_val = baseline["mean"]
        delta = baseline["threshold_delta"]
        spikes = []
        for obs in baseline["participating_observations"]:
            val = float(obs["value"])
            deviation = abs(val - mean_val)
            if deviation > delta:
                spikes.append({
                    "spike_id": self._next_id("SPIKE-REV"),
                    "window_key": window_key,
                    "point_key": window["point_key"],
                    "segment": window["segment"],
                    "timestamp": obs["timestamp"],
                    "value": val,
                    "baseline_mean": mean_val,
                    "threshold_delta": delta,
                    "actual_delta": round(deviation, 6),
                    "baseline_id": chosen_id,
                    "disposition": "待复核",
                    "flagged_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
                })
        # 覆盖写待复核尖峰文件：待复核尖峰只反映当前获准基线的判定
        path = self._spike_file(window_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for s in spikes:
                f.write(json.dumps(s, ensure_ascii=False, default=str) + "\n")
        self._pending_spikes[window_key] = spikes
        window["status"] = WINDOW_PENDING
        self._persist_window(window)
        self._log_event(window_key, "review_submitted", {
            "baseline_id": chosen_id, "pending_spike_count": len(spikes),
            "spike_ids": [s["spike_id"] for s in spikes],
        })
        return {
            "window_key": window_key,
            "point_key": window["point_key"],
            "segment": window["segment"],
            "adopted_baseline_id": chosen_id,
            "baseline_mean": mean_val,
            "threshold_delta": delta,
            "participating_fingerprint": current_fp,
            "pending_spike_count": len(spikes),
            "pending_spikes": spikes,
        }

    def confirm_window(self, window_key: str, *, approver: str, note: str = "") -> dict[str, Any]:
        """旧班/维护人员确认窗口结论（如确认升速后高值为生产数据），基线随之冻结。"""
        window = self._get_window(window_key)
        if not window["frozen_baseline_id"]:
            return {"error": "窗口还没有获准基线，不能确认；请先批准基线再确认窗口"}
        window["status"] = WINDOW_CONFIRMED
        window["confirmed_by"] = approver
        window["confirm_note"] = note
        window["confirmed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
        self._persist_window(window)
        self._log_event(window_key, "window_confirmed", {
            "approver": approver, "note": note,
            "frozen_baseline_id": window["frozen_baseline_id"],
        })
        return dict(window)

    # ------------------------------------------------------------------
    # 5. 对照视图：参与观测 / 隔离点 / 启停记录 / 尖峰
    # ------------------------------------------------------------------
    def comparison_view(self, window_key: str) -> dict[str, Any]:
        window = self._get_window(window_key)
        baselines = self._baselines.get(window_key, [])
        approved = next((b for b in baselines if b["status"] == BASELINE_APPROVED), None)
        return {
            "window": dict(window),
            "valid_window": {"start": window["start_ts"], "end": window["end_ts"]},
            "threshold_multiplier": window["threshold_multiplier"],
            "participating_observations": [
                {"timestamp": o["timestamp"], "value": o["value"], "source": o["source"]}
                for o in self._participating_observations(window_key)
            ],
            "isolated_points": [
                {"timestamp": o["timestamp"], "value": o["value"],
                 "source": o["source"], "reason": o["isolation_reason"],
                 "received_at": o["received_at"]}
                for o in self._isolated_observations(window_key)
            ],
            "start_stop_events": [
                {"event_id": e["event_id"], "kind": e["kind"], "at": e["at"],
                 "detail": e["detail"]}
                for e in self._events.get(window_key, [])
            ],
            "baselines": [
                {
                    "baseline_id": b["baseline_id"], "status": b["status"],
                    "reviewer": b["reviewer"], "basis_note": b.get("basis_note", ""),
                    "mean": b["mean"], "threshold_delta": b["threshold_delta"],
                    "participating_count": b["participating_count"],
                    "participating_fingerprint": b["participating_fingerprint"],
                    "late_points_excluded": b.get("late_points_excluded", 0),
                    "created_at": b["created_at"],
                    "approver": b.get("approver", ""),
                    "reject_reason": b.get("reject_reason", ""),
                }
                for b in baselines
            ],
            "approved_baseline_id": window["frozen_baseline_id"],
            "active_baseline_id": window["active_baseline_id"],
            "pending_spikes": self._pending_spikes.get(window_key, []),
            "pending_spike_count": len(self._pending_spikes.get(window_key, [])),
            "reproducible": (
                self._fingerprint(self._participating_observations(window_key))
                == approved["participating_fingerprint"]
            ) if approved else False,
        }

    def list_windows(self, point_key: str | None = None) -> list[dict[str, Any]]:
        windows = list(self._windows.values())
        if point_key:
            windows = [w for w in windows if w["point_key"] == point_key]
        return [dict(w) for w in windows]

    def add_equipment_event(self, window_key: str, kind: str, detail: dict[str, Any]) -> dict[str, Any]:
        """登记设备启停/升速事件，纳入对照视图的启停记录。"""
        self._get_window(window_key)
        self._log_event(window_key, kind, detail)
        return {"window_key": window_key, "kind": kind}
