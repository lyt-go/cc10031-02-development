"""尖峰分段复验集成测试。

覆盖业务规则：
1. 升速前后各一段窗口，POST 窗口已确认且基线冻结；
2. POST 确认后迟到的数据被隔离，不进入原基线（后到数据不混作原基线）；
3. PRE 窗口存在两份可查的前段基线依据（A 待批准/被取代，B 已获准）；
4. 一次正式复验对同一窗口不能混合两个基线；
5. 完成前段复验时核对实际采用的基线与送往待复核的尖峰数；
6. 对照视图包含参与计算的连续观测、隔离点、启停记录；
7. 基线快照可复现（数据指纹一致）。
"""
from __future__ import annotations

import math
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.spike_review import (
    BASELINE_APPROVED,
    BASELINE_REJECTED,
    SOURCE_LATE,
    WINDOW_CONFIRMED,
    WINDOW_REVIEWING,
    SpikeReviewService,
)
from app.services import spike_review as review_module
from app.services.spike_review_scenario import (
    POINT,
    POST_LATE_AFTER_CONFIRM,
    POST_WINDOW,
    PRE_INITIAL,
    PRE_LATE_STABLE,
    PRE_WINDOW,
    build_scenario,
)


@pytest.fixture(autouse=True)
def setup_data_dir():
    with tempfile.TemporaryDirectory() as tmp:
        with patch("app.services.base.DATA_ROOT", Path(tmp)):
            review_module.DATA_ROOT = Path(tmp)
            yield Path(tmp)


@pytest.fixture()
def scenario():
    svc = SpikeReviewService()
    ids = build_scenario(svc)
    return svc, ids


class TestSegmentedReviewScenario:
    def test_two_windows_registered_with_valid_ranges(self, scenario):
        svc, ids = scenario
        windows = {w["window_key"]: w for w in svc.list_windows(POINT)}
        assert set(windows) == {PRE_WINDOW, POST_WINDOW}
        assert windows[POST_WINDOW]["status"] == WINDOW_CONFIRMED
        # 升速前窗口：复验已发起但未提交，仍处复验中
        assert windows[PRE_WINDOW]["status"] == WINDOW_REVIEWING

    def test_post_window_frozen_and_late_data_isolated(self, scenario):
        svc, ids = scenario
        view = svc.comparison_view(POST_WINDOW)
        # 冻结基线存在，复现指纹通过
        assert view["approved_baseline_id"] == ids["post_baseline_id"]
        assert view["reproducible"] is True
        # 确认后到达的点被隔离，且未进入任何参与计算观测
        isolated_ts = {p["timestamp"] for p in view["isolated_points"]}
        late_ts = {ts for ts, _ in POST_LATE_AFTER_CONFIRM}
        assert late_ts <= isolated_ts
        participating_ts = {o["timestamp"] for o in view["participating_observations"]}
        assert late_ts.isdisjoint(participating_ts)
        # 隔离点带有明确原因
        assert all("隔离" in p["reason"] for p in view["isolated_points"])

    def test_frozen_window_rejects_new_baseline(self, scenario):
        svc, ids = scenario
        result = svc.compute_baseline(
            POST_WINDOW, reviewer="任何人", basis_note="试图用迟到数据重算")
        assert "error" in result
        assert "冻结" in result["error"]

    def test_pre_window_keeps_two_queryable_baselines(self, scenario):
        svc, ids = scenario
        view = svc.comparison_view(PRE_WINDOW)
        by_id = {b["baseline_id"]: b for b in view["baselines"]}
        # 两份前段依据都可查
        assert ids["baseline_a_id"] in by_id
        assert ids["baseline_b_id"] in by_id
        # 基线 B 正式获准；基线 A 被取代但仍保留可查
        assert by_id[ids["baseline_b_id"]]["status"] == BASELINE_APPROVED
        assert by_id[ids["baseline_a_id"]]["status"] == BASELINE_REJECTED
        assert view["approved_baseline_id"] == ids["baseline_b_id"]

    def test_late_stable_values_lower_pre_baseline(self, scenario):
        svc, ids = scenario
        view = svc.comparison_view(PRE_WINDOW)
        by_id = {b["baseline_id"]: b for b in view["baselines"]}
        baseline_a = by_id[ids["baseline_a_id"]]
        baseline_b = by_id[ids["baseline_b_id"]]
        # A 只含初始 8 个观测；B 补入 3 个迟到平稳值共 11 个
        assert baseline_a["participating_count"] == len(PRE_INITIAL)
        assert baseline_b["participating_count"] == len(PRE_INITIAL) + len(PRE_LATE_STABLE)
        assert baseline_b["late_points_excluded"] == 0  # PRE 未冻结，迟到点未被隔离
        # 迟到平稳值拉低了前段基线
        assert baseline_b["mean"] < baseline_a["mean"]
        expected_mean_b = (sum(v for _, v in PRE_INITIAL)
                           + sum(v for _, v in PRE_LATE_STABLE)) / (
                               len(PRE_INITIAL) + len(PRE_LATE_STABLE))
        assert math.isclose(baseline_b["mean"], round(expected_mean_b, 6), rel_tol=1e-9)

    def test_cannot_mix_two_baselines_in_one_review(self, scenario):
        svc, ids = scenario
        # 窗口正式获准的是 B，若强行指定 A 提交复验必须被拒绝
        result = svc.submit_review(PRE_WINDOW, baseline_id=ids["baseline_a_id"])
        assert "error" in result
        assert "混合两个基线" in result["error"]

    def test_second_approval_on_same_window_rejected(self, scenario):
        svc, ids = scenario
        # B 已获准，再批准 A 必须被拒绝（一次复验只允许一条获准基线）
        result = svc.approve_baseline(
            PRE_WINDOW, ids["baseline_a_id"], approver="试图再批")
        assert "error" in result
        assert "不能混合两个基线" in result["error"]

    def test_submit_pre_review_adopts_baseline_b_and_flags_two_spikes(self, scenario):
        svc, ids = scenario
        result = svc.submit_review(PRE_WINDOW)
        assert "error" not in result
        # 核对实际采用的基线：B（含 3 个迟到平稳值的更正基线）
        assert result["adopted_baseline_id"] == ids["baseline_b_id"]
        # 核对这一步送往待复核的尖峰数：2 个升速前短暂高值
        assert result["pending_spike_count"] == 2
        flagged_ts = {s["timestamp"] for s in result["pending_spikes"]}
        assert flagged_ts == {"2026-11-20T08:10:00", "2026-11-20T08:11:00"}
        # 所有待复核尖峰都带有可复现的基线编号与判定量
        for spike in result["pending_spikes"]:
            assert spike["baseline_id"] == ids["baseline_b_id"]
            assert spike["disposition"] == "待复核"
            assert spike["actual_delta"] > spike["threshold_delta"]

    def test_baseline_a_alone_would_not_flag_the_two_high_values(self):
        """旁证：旧基线 A 均值偏高，两个高值并未越阈；正是迟到平稳值拉低基线后才成为尖峰。"""
        svc = SpikeReviewService()
        # 只装载 PRE 初始观测并计算基线 A，不补迟到数据
        svc.register_window(
            POINT, "PRE2", "2026-11-20T08:00:00", "2026-11-20T09:00:00",
            threshold_multiplier=0.5)
        key = f"RW-{POINT}-PRE2"
        svc.load_observations(key, [
            {"timestamp": ts, "value": v} for ts, v in PRE_INITIAL])
        baseline = svc.compute_baseline(key, reviewer="复核员甲")
        svc.approve_baseline(key, baseline["baseline_id"], approver="质检负责人")
        result = svc.submit_review(key)
        assert result["pending_spike_count"] == 0

    def test_comparison_view_contains_all_required_sections(self, scenario):
        svc, ids = scenario
        view = svc.comparison_view(PRE_WINDOW)
        # 对照视图四要素齐全
        assert len(view["participating_observations"]) == len(PRE_INITIAL) + len(PRE_LATE_STABLE)
        assert "isolated_points" in view
        assert len(view["start_stop_events"]) >= 1
        ramp_events = [e for e in view["start_stop_events"] if e["kind"] == "equipment_ramp_up"]
        assert ramp_events and ramp_events[0]["detail"]["at"] == "2026-11-20T09:00:00"
        # 有效窗与阈值口径随视图返回
        assert view["valid_window"]["start"] == "2026-11-20T08:00:00"
        assert view["threshold_multiplier"] == 0.5

    def test_artifacts_persisted_for_reproducibility(self, scenario, setup_data_dir):
        svc, ids = scenario
        review_dir = setup_data_dir / "spike_review"
        # 窗口、基线、观测、事件、待复核尖峰文件均落盘
        assert (review_dir / f"{PRE_WINDOW}_window.json").exists()
        assert (review_dir / f"{PRE_WINDOW}_baselines.jsonl").exists()
        assert (review_dir / f"{PRE_WINDOW}_observations.jsonl").exists()
        assert (review_dir / f"{PRE_WINDOW}_events.jsonl").exists()
        # 提交复验后待复核尖峰落盘
        result = svc.submit_review(PRE_WINDOW)
        spike_file = review_dir / f"{PRE_WINDOW}_pending_spikes.jsonl"
        assert spike_file.exists()
        lines = [line for line in spike_file.read_text(encoding="utf-8").splitlines() if line]
        assert len(lines) == result["pending_spike_count"] == 2

    def test_fingerprint_rejects_tampered_baseline(self, scenario):
        """若参与计算观测被篡改，指纹不一致时正式复验必须拒绝。"""
        svc, ids = scenario
        # 手工篡改一个参与观测的值
        for obs in svc._observations[PRE_WINDOW]:
            if not obs["isolated"]:
                obs["value"] = obs["value"] + 5.0
                break
        result = svc.submit_review(PRE_WINDOW)
        assert "error" in result
        assert "不可复现" in result["error"]

    def test_late_data_to_unconfirmed_window_is_accepted_not_isolated(self, scenario):
        svc, ids = scenario
        # PRE 窗口在冻结前收到的迟到平稳值应进入参与集合而非隔离
        view = svc.comparison_view(PRE_WINDOW)
        late_ts = {ts for ts, _ in PRE_LATE_STABLE}
        participating_ts = {o["timestamp"] for o in view["participating_observations"]}
        isolated_ts = {p["timestamp"] for p in view["isolated_points"]}
        assert late_ts <= participating_ts
        assert late_ts.isdisjoint(isolated_ts)

    def test_submit_twice_keeps_single_baseline_and_same_count(self, scenario):
        svc, ids = scenario
        first = svc.submit_review(PRE_WINDOW)
        second = svc.submit_review(PRE_WINDOW)
        assert first["adopted_baseline_id"] == second["adopted_baseline_id"] == ids["baseline_b_id"]
        assert first["pending_spike_count"] == second["pending_spike_count"] == 2

    def test_post_window_rejects_late_source_even_before_rerun(self, scenario):
        """已确认窗口再次装载迟到数据，返回隔离计数且不新增参与观测。"""
        svc, ids = scenario
        before = len(svc.comparison_view(POST_WINDOW)["participating_observations"])
        result = svc.load_observations(POST_WINDOW, [
            {"timestamp": "2026-11-20T09:50:00", "value": 10.6}], source=SOURCE_LATE)
        assert result["isolated"] == 1
        assert result["accepted"] == 0
        after = len(svc.comparison_view(POST_WINDOW)["participating_observations"])
        assert before == after

    def test_empty_window_cannot_compute_baseline(self, setup_data_dir):
        svc = SpikeReviewService()
        svc.register_window(POINT, "EMPTY", "2026-11-20T07:00:00", "2026-11-20T08:00:00")
        result = svc.compute_baseline(f"RW-{POINT}-EMPTY", reviewer="甲")
        assert "error" in result
