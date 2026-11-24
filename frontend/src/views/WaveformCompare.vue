<template>
  <div class="waveform-wrap">
    <svg class="waveform-svg" :viewBox="`0 0 ${width} ${height}`" role="img" aria-label="尖峰复验波形对照">
      <!-- 阈值带：均值 ± 阈值增量 -->
      <rect
        v-if="mean !== null && delta !== null"
        :x="padLeft"
        :y="yScale(mean + delta)"
        :width="plotWidth"
        :height="Math.max(yScale(mean - delta) - yScale(mean + delta), 0)"
        class="band-threshold"
      />
      <!-- 均值线 -->
      <line
        v-if="mean !== null"
        :x1="padLeft"
        :x2="width - padRight"
        :y1="yScale(mean)"
        :y2="yScale(mean)"
        class="line-mean"
      />
      <text v-if="mean !== null" :x="width - padRight + 4" :y="yScale(mean) + 3" class="svg-label mean-label">
        均值 {{ fmt(mean) }}
      </text>
      <!-- 参与计算的连续观测折线 -->
      <polyline
        v-if="points.length"
        :points="linePoints"
        class="line-series"
      />
      <!-- 参与观测点 -->
      <circle
        v-for="(p, idx) in points"
        :key="`p-${idx}`"
        :cx="p.x"
        :cy="p.y"
        :r="spikeTimestamps.has(p.timestamp) ? 5 : 3"
        :class="spikeTimestamps.has(p.timestamp) ? 'dot-spike' : 'dot-normal'"
      >
        <title>{{ p.timestamp }}｜值 {{ fmt(p.value) }}｜{{ sourceLabel(p.source) }}</title>
      </circle>
      <!-- 被隔离的点（不参与阈值计算，单独标红） -->
      <g v-for="(p, idx) in isolatedPoints" :key="`iso-${idx}`">
        <line :x1="p.x - 4" :y1="p.y - 4" :x2="p.x + 4" :y2="p.y + 4" class="mark-isolated" />
        <line :x1="p.x - 4" :y1="p.y + 4" :x2="p.x + 4" :y2="p.y - 4" class="mark-isolated" />
        <title>隔离点 {{ p.timestamp }}｜值 {{ fmt(p.value) }}｜{{ p.reason }}</title>
      </g>
      <!-- 纵轴刻度 -->
      <text v-for="tick in yTicks" :key="`tick-${tick}`" :x="4" :y="yScale(tick) + 3" class="svg-label">
        {{ fmt(tick) }}
      </text>
    </svg>
    <ul class="waveform-legend">
      <li><span class="legend-dot dot-normal" />参与阈值计算的连续观测</li>
      <li><span class="legend-dot dot-spike" />送往待复核的尖峰</li>
      <li><span class="legend-x" />被隔离、不参与基线的点</li>
      <li><span class="legend-band" />阈值带（均值 ± {{ delta !== null ? fmt(delta) : '—' }}）</li>
    </ul>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import type { IsolatedPoint, ObservationRow, PendingSpike } from '@/api/spike-review'

const props = defineProps<{
  participating: ObservationRow[]
  isolated: IsolatedPoint[]
  spikes: PendingSpike[]
  mean: number | null
  delta: number | null
}>()

const width = 900
const height = 240
const padLeft = 48
const padRight = 92

const allValues = computed(() => {
  const values = [
    ...props.participating.map((o) => o.value),
    ...props.isolated.map((o) => o.value),
  ]
  if (props.mean !== null) values.push(props.mean)
  if (props.mean !== null && props.delta !== null) {
    values.push(props.mean + props.delta, props.mean - props.delta)
  }
  return values
})

const minV = computed(() => (allValues.value.length ? Math.min(...allValues.value) : 0))
const maxV = computed(() => (allValues.value.length ? Math.max(...allValues.value) : 1))
const span = computed(() => Math.max(maxV.value - minV.value, 0.0001))
const plotTop = 16
const plotBottom = height - 28
const plotWidth = width - padLeft - padRight

function yScale(v: number): number {
  const ratio = (v - minV.value) / span.value
  return plotBottom - ratio * (plotBottom - plotTop)
}

// 所有观测（含隔离点）按时间合并后统一分配横坐标，保证波形时序连续
const merged = computed(() => {
  const rows = [
    ...props.participating.map((o) => ({
      timestamp: o.timestamp, value: o.value, kind: 'participating' as const,
      source: o.source, reason: '',
    })),
    ...props.isolated.map((o) => ({
      timestamp: o.timestamp, value: o.value, kind: 'isolated' as const,
      source: o.source, reason: o.reason,
    })),
  ]
  return rows.sort((a, b) => (a.timestamp < b.timestamp ? -1 : 1))
})

function xScale(index: number): number {
  if (merged.value.length <= 1) return padLeft + plotWidth / 2
  return padLeft + (index / (merged.value.length - 1)) * plotWidth
}

const points = computed(() =>
  merged.value
    .map((row, idx) => ({ ...row, x: xScale(idx), y: yScale(row.value) }))
    .filter((row) => row.kind === 'participating'),
)

const isolatedPoints = computed(() =>
  merged.value
    .map((row, idx) => ({ ...row, x: xScale(idx), y: yScale(row.value) }))
    .filter((row) => row.kind === 'isolated'),
)

const linePoints = computed(() => points.value.map((p) => `${p.x},${p.y}`).join(' '))
const spikeTimestamps = computed(() => new Set(props.spikes.map((s) => s.timestamp)))

const yTicks = computed(() => {
  const ticks: number[] = []
  for (let i = 0; i <= 4; i += 1) {
    ticks.push(minV.value + (span.value * i) / 4)
  }
  return ticks
})

function fmt(v: number): string {
  return Number(v).toFixed(2)
}

function sourceLabel(source: string): string {
  if (source === 'late_arrival') return '迟到补入观测'
  if (source === 'isolated') return '隔离点'
  return '原始观测'
}
</script>

<style scoped>
.waveform-wrap {
  border: 1px solid var(--border);
  border-radius: 8px;
  background: #fff;
  padding: 12px;
}
.waveform-svg {
  width: 100%;
  height: auto;
}
.band-threshold {
  fill: rgb(31 111 235 / 10%);
  stroke: none;
}
.line-mean {
  stroke: #1f6feb;
  stroke-width: 1.2;
  stroke-dasharray: 6 4;
}
.line-series {
  fill: none;
  stroke: #475569;
  stroke-width: 1.6;
}
.dot-normal {
  fill: #1f6feb;
  stroke: #fff;
  stroke-width: 1;
}
.dot-spike {
  fill: #f59e0b;
  stroke: #b45309;
  stroke-width: 1.4;
}
.mark-isolated {
  stroke: #dc2626;
  stroke-width: 2;
}
.svg-label {
  font-size: 10px;
  fill: #64748b;
}
.mean-label {
  fill: #1f6feb;
}
.waveform-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
  font-size: 12px;
  color: var(--muted);
}
.legend-dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  margin-right: 5px;
  vertical-align: middle;
}
.legend-dot.dot-normal {
  background: #1f6feb;
}
.legend-dot.dot-spike {
  background: #f59e0b;
}
.legend-x {
  display: inline-block;
  width: 10px;
  height: 10px;
  margin-right: 5px;
  vertical-align: middle;
  position: relative;
}
.legend-x::before,
.legend-x::after {
  content: '';
  position: absolute;
  left: 4px;
  top: 0;
  width: 2px;
  height: 12px;
  background: #dc2626;
}
.legend-x::before {
  transform: rotate(45deg);
}
.legend-x::after {
  transform: rotate(-45deg);
}
.legend-band {
  display: inline-block;
  width: 14px;
  height: 10px;
  border-radius: 2px;
  background: rgb(31 111 235 / 10%);
  border: 1px solid rgb(31 111 235 / 35%);
  margin-right: 5px;
  vertical-align: middle;
}
</style>
