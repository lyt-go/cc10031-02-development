/** 尖峰分段复验接口封装：窗口、基线、隔离点、待复核尖峰与对照视图。 */
import { request } from './client'

export type WindowRow = {
  window_key: string
  point_key: string
  segment: string
  start_ts: string
  end_ts: string
  threshold_multiplier: number
  status: string
  reviewer: string
  note: string
  frozen_baseline_id: string | null
  active_baseline_id: string | null
  created_at: string
}

export type ObservationRow = {
  timestamp: string
  value: number
  source: string
}

export type IsolatedPoint = {
  timestamp: string
  value: number
  source: string
  reason: string
  received_at: string
}

export type StartStopEvent = {
  event_id: string
  kind: string
  at: string
  detail: Record<string, unknown>
}

export type BaselineRow = {
  baseline_id: string
  status: string
  reviewer: string
  basis_note: string
  mean: number
  threshold_delta: number
  participating_count: number
  participating_fingerprint: string
  late_points_excluded: number
  created_at: string
  approver: string
  reject_reason: string
}

export type PendingSpike = {
  spike_id: string
  window_key: string
  point_key: string
  segment: string
  timestamp: string
  value: number
  baseline_mean: number
  threshold_delta: number
  actual_delta: number
  baseline_id: string
  disposition: string
  flagged_at: string
}

export type ComparisonView = {
  window: WindowRow
  valid_window: { start: string; end: string }
  threshold_multiplier: number
  participating_observations: ObservationRow[]
  isolated_points: IsolatedPoint[]
  start_stop_events: StartStopEvent[]
  baselines: BaselineRow[]
  approved_baseline_id: string | null
  active_baseline_id: string | null
  pending_spikes: PendingSpike[]
  pending_spike_count: number
  reproducible: boolean
}

async function postJson<T>(path: string, payload: Record<string, unknown> = {}): Promise<T> {
  const response = await request(path, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  const body = (await response.json()) as T & { detail?: string }
  if (!response.ok) throw new Error(body.detail ?? '接口处理失败')
  return body
}

export async function initScenario(): Promise<Record<string, unknown>> {
  return postJson('/api/spike-review/scenario/init')
}

export async function fetchWindows(pointKey?: string): Promise<{ items: WindowRow[]; total: number }> {
  const query = pointKey ? `?point_key=${encodeURIComponent(pointKey)}` : ''
  const response = await request(`/api/spike-review/windows${query}`)
  if (!response.ok) throw new Error('复验窗口读取失败')
  return await response.json()
}

export async function fetchComparison(windowKey: string): Promise<ComparisonView> {
  const response = await request(`/api/spike-review/comparison/${encodeURIComponent(windowKey)}`)
  if (!response.ok) {
    const body = (await response.json()) as { detail?: string }
    throw new Error(body.detail ?? '对照视图读取失败')
  }
  return await response.json()
}

export async function computeBaseline(
  windowKey: string,
  reviewer: string,
  basisNote: string,
): Promise<Record<string, unknown>> {
  return postJson('/api/spike-review/baselines/compute', {
    window_key: windowKey,
    reviewer,
    basis_note: basisNote,
  })
}

export async function approveBaseline(
  windowKey: string,
  baselineId: string,
  approver: string,
): Promise<Record<string, unknown>> {
  return postJson('/api/spike-review/baselines/approve', {
    window_key: windowKey,
    baseline_id: baselineId,
    approver,
  })
}

export async function rejectBaseline(
  windowKey: string,
  baselineId: string,
  reason: string,
): Promise<Record<string, unknown>> {
  return postJson('/api/spike-review/baselines/reject', {
    window_key: windowKey,
    baseline_id: baselineId,
    reason,
  })
}

export async function submitReview(
  windowKey: string,
  baselineId?: string,
): Promise<{
  adopted_baseline_id: string
  baseline_mean: number
  threshold_delta: number
  pending_spike_count: number
  pending_spikes: PendingSpike[]
}> {
  return postJson('/api/spike-review/reviews/submit', {
    window_key: windowKey,
    baseline_id: baselineId ?? null,
  })
}

export async function confirmWindow(
  windowKey: string,
  approver: string,
  note: string,
): Promise<Record<string, unknown>> {
  return postJson('/api/spike-review/windows/confirm', {
    window_key: windowKey,
    approver,
    note,
  })
}

export async function loadObservations(
  windowKey: string,
  observations: { timestamp: string; value: number }[],
  source: 'original' | 'late_arrival' = 'original',
): Promise<{ accepted: number; isolated: number; frozen: boolean }> {
  return postJson('/api/spike-review/observations', {
    window_key: windowKey,
    observations,
    source,
  })
}
