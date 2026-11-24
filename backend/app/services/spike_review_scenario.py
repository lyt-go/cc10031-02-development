"""尖峰分段复验标准业务场景构建。

复现题目情形：
- 测点 SENS-3301 在设备升速（09:00:00）前后各有一段短暂高值；
- 升速后（POST）窗口：维护人员已批准观测、旧班确认是生产数据，基线冻结；
- 升速前（PRE）窗口：第一位复核员已发起复验（基线 A）但未提交；
- 随后 3 个升速前平稳值迟到，第二位复核员据此完成更正计算（基线 B）并获准；
- 两份前段依据都可查；对已确认的 POST 窗口再到的数据一律隔离。
"""
from __future__ import annotations

from typing import Any

from app.services.spike_review import (
    SOURCE_LATE,
    SOURCE_ORIGINAL,
    SpikeReviewService,
)

POINT = "SENS-3301"
PRE_WINDOW = f"RW-{POINT}-PRE"
POST_WINDOW = f"RW-{POINT}-POST"
RAMP_UP_TS = "2026-11-20T09:00:00"

# 升速前初始观测（复验发起时已在有效窗内）：6 个平稳值 + 2 个短暂高值
PRE_INITIAL = [
    ("2026-11-20T08:00:00", 10.0),
    ("2026-11-20T08:01:00", 10.1),
    ("2026-11-20T08:02:00", 9.9),
    ("2026-11-20T08:10:00", 17.0),   # 短暂高值 1
    ("2026-11-20T08:11:00", 17.0),   # 短暂高值 2
    ("2026-11-20T08:20:00", 10.0),
    ("2026-11-20T08:21:00", 10.2),
    ("2026-11-20T08:22:00", 9.8),
]

# 复验发起后才迟到的 3 个升速前平稳值
PRE_LATE_STABLE = [
    ("2026-11-20T08:30:00", 9.9),
    ("2026-11-20T08:31:00", 10.0),
    ("2026-11-20T08:32:00", 10.1),
]

# 升速后观测：含短暂高值，维护人员确认为生产数据
POST_OBSERVATIONS = [
    ("2026-11-20T09:00:00", 10.4),
    ("2026-11-20T09:01:00", 10.6),
    ("2026-11-20T09:02:00", 10.5),
    ("2026-11-20T09:10:00", 16.5),   # 升速后短暂高值（生产数据）
    ("2026-11-20T09:11:00", 17.0),   # 升速后短暂高值（生产数据）
    ("2026-11-20T09:20:00", 10.5),
    ("2026-11-20T09:21:00", 10.6),
    ("2026-11-20T09:22:00", 10.4),
]

# POST 窗口确认之后才到达的观测：必须被隔离，不能混入已冻结基线
POST_LATE_AFTER_CONFIRM = [
    ("2026-11-20T09:40:00", 10.5),
]


def build_scenario(service: SpikeReviewService) -> dict[str, Any]:
    """把标准业务场景装载到给定服务实例，返回关键标识供测试与界面使用。"""
    # 1. 登记两个分段窗口（有效窗）
    service.register_window(
        POINT, "PRE", "2026-11-20T08:00:00", "2026-11-20T09:00:00",
        threshold_multiplier=0.5, reviewer="复核员甲",
        note="设备升速前窗口，旧班未确认",
    )
    service.register_window(
        POINT, "POST", "2026-11-20T09:00:00", "2026-11-20T10:00:00",
        threshold_multiplier=0.5, reviewer="维护人员",
        note="设备升速后窗口，高值疑为生产数据",
    )

    # 2. 设备启停/升速事件进入启停记录
    service.add_equipment_event(PRE_WINDOW, "equipment_ramp_up", {
        "at": RAMP_UP_TS, "note": "设备开始升速，PRE 窗口结束",
    })
    service.add_equipment_event(POST_WINDOW, "equipment_ramp_up", {
        "at": RAMP_UP_TS, "note": "设备开始升速，POST 窗口开始",
    })

    # 3. 升速后窗口：装载观测 → 计算基线 → 维护人员批准 → 旧班确认（冻结）
    service.load_observations(POST_WINDOW, [
        {"timestamp": ts, "value": v} for ts, v in POST_OBSERVATIONS
    ], source=SOURCE_ORIGINAL)
    post_baseline = service.compute_baseline(
        POST_WINDOW, reviewer="维护人员", basis_note="升速后原始观测基线")
    service.approve_baseline(POST_WINDOW, post_baseline["baseline_id"], approver="维护主管")
    service.confirm_window(
        POST_WINDOW, approver="旧班值班长",
        note="升速后高值确认为生产数据，窗口结论生效，基线冻结",
    )
    # 确认之后再到的数据：必须隔离
    late_result = service.load_observations(POST_WINDOW, [
        {"timestamp": ts, "value": v} for ts, v in POST_LATE_AFTER_CONFIRM
    ], source=SOURCE_LATE)

    # 4. 升速前窗口：第一位复核员发起复验（基线 A），但未提交
    service.load_observations(PRE_WINDOW, [
        {"timestamp": ts, "value": v} for ts, v in PRE_INITIAL
    ], source=SOURCE_ORIGINAL)
    baseline_a = service.compute_baseline(
        PRE_WINDOW, reviewer="复核员甲",
        basis_note="初次复验：仅基于发起时已在窗内的观测",
    )

    # 5. 3 个升速前平稳值迟到（窗口未确认，可进入后续更正计算）
    pre_late_result = service.load_observations(PRE_WINDOW, [
        {"timestamp": ts, "value": v} for ts, v in PRE_LATE_STABLE
    ], source=SOURCE_LATE)

    # 6. 第二位复核员完成更正计算（基线 B）并正式获准
    baseline_b = service.compute_baseline(
        PRE_WINDOW, reviewer="复核员乙",
        basis_note="更正复验：补入 3 个迟到平稳值后重算前段基线",
    )
    approve_result = service.approve_baseline(
        PRE_WINDOW, baseline_b["baseline_id"], approver="质检负责人",
    )

    return {
        "point": POINT,
        "pre_window": PRE_WINDOW,
        "post_window": POST_WINDOW,
        "baseline_a_id": baseline_a["baseline_id"],
        "baseline_b_id": baseline_b["baseline_id"],
        "post_baseline_id": post_baseline["baseline_id"],
        "post_late_isolated": late_result["isolated"],
        "pre_late_accepted": pre_late_result["accepted"],
    }
