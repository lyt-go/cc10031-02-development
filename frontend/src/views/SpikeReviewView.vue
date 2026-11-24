<template>
  <section class="page" data-module="spike_review">
    <header class="page-head">
      <div>
        <h2>尖峰分段复验与波形对照</h2>
        <p class="page-desc">
          按设备升速前后的有效窗逐段复验：查看实际参与阈值计算的连续观测、被隔离的点与启停记录，
          已确认窗口的基线冻结，后到数据不混入原基线；一次正式复验只采用一条获准基线。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" :disabled="loading" @click="resetScenario">
          重置标准复验场景
        </button>
      </div>
    </header>

    <p v-if="message" class="status-banner" :class="messageKind">{{ message }}</p>

    <!-- 窗口切换 -->
    <div class="window-tabs">
      <button
        v-for="w in windows"
        :key="w.window_key"
        type="button"
        class="window-tab"
        :class="{ active: w.window_key === selectedKey }"
        @click="selectWindow(w.window_key)"
      >
        <strong>{{ w.segment === 'PRE' ? '升速前段（未确认）' : '升速后段（旧班已确认）' }}</strong>
        <span class="tab-meta">{{ w.window_key }}｜{{ w.status }}</span>
      </button>
    </div>

    <div v-if="view" class="review-body">
      <!-- 窗口与有效窗信息 -->
      <div class="stat-row">
        <article class="stat-card">
          <span class="stat-label">窗口状态</span>
          <strong class="stat-value">{{ view.window.status }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">有效窗（启停边界）</span>
          <strong class="stat-value small">{{ view.valid_window.start }} ～ {{ view.valid_window.end }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">阈值口径</span>
          <strong class="stat-value small">偏离 &gt; {{ view.threshold_multiplier }} × 均值</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">送往待复核尖峰数</span>
          <strong class="stat-value">{{ view.pending_spike_count }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">基线可复现</span>
          <strong class="stat-value" :class="view.reproducible ? 'ok' : 'warn'">
            {{ view.reproducible ? '指纹一致' : '指纹不一致' }}
          </strong>
        </article>
      </div>

      <!-- 波形对照 -->
      <h3 class="section-title">波形对照（参与观测 / 隔离点 / 阈值带 / 尖峰）</h3>
      <WaveformCompare
        :participating="view.participating_observations"
        :isolated="view.isolated_points"
        :spikes="view.pending_spikes"
        :mean="adoptedBaseline?.mean ?? null"
        :delta="adoptedBaseline?.threshold_delta ?? null"
      />

      <!-- 基线版本对照：两份前段依据都可查 -->
      <h3 class="section-title">基线版本对照（同一窗口一次复验只能获准一条）</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>基线编号</th>
            <th>状态</th>
            <th>复核员/依据说明</th>
            <th>参与观测数</th>
            <th>基线均值</th>
            <th>阈值增量</th>
            <th>排除迟到点</th>
            <th>数据指纹</th>
            <th>批准信息</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="b in view.baselines"
            :key="b.baseline_id"
            :class="{ 'row-approved': b.baseline_id === view.approved_baseline_id }"
          >
            <td>{{ b.baseline_id }}</td>
            <td>
              <span class="status-pill" :data-status="b.status">{{ b.status }}</span>
            </td>
            <td>{{ b.reviewer }}<br /><span class="muted">{{ b.basis_note }}</span></td>
            <td>{{ b.participating_count }}</td>
            <td>{{ b.mean }}</td>
            <td>{{ b.threshold_delta }}</td>
            <td>{{ b.late_points_excluded }}</td>
            <td><code class="fingerprint">{{ b.participating_fingerprint }}</code></td>
            <td>
              <template v-if="b.approver">{{ b.approver }}</template>
              <span v-else-if="b.reject_reason" class="warn">{{ b.reject_reason }}</span>
              <span v-else class="muted">待批准</span>
            </td>
          </tr>
        </tbody>
      </table>

      <!-- 明细三栏：参与观测 / 隔离点 / 启停记录 -->
      <div class="detail-grid">
        <section class="detail-panel">
          <h4>实际参与阈值计算的连续观测（{{ view.participating_observations.length }}）</h4>
          <div class="scroll-box">
            <table class="data-table compact">
              <thead><tr><th>采样时刻</th><th>观测值</th><th>来源</th></tr></thead>
              <tbody>
                <tr v-for="o in view.participating_observations" :key="o.timestamp">
                  <td>{{ o.timestamp.slice(11) }}</td>
                  <td>{{ o.value }}</td>
                  <td>{{ sourceLabel(o.source) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section class="detail-panel">
          <h4>被隔离的点（{{ view.isolated_points.length }}）</h4>
          <div class="scroll-box">
            <table v-if="view.isolated_points.length" class="data-table compact">
              <thead><tr><th>采样时刻</th><th>观测值</th><th>隔离原因</th></tr></thead>
              <tbody>
                <tr v-for="p in view.isolated_points" :key="p.timestamp" class="row-isolated">
                  <td>{{ p.timestamp.slice(11) }}</td>
                  <td>{{ p.value }}</td>
                  <td>{{ p.reason }}</td>
                </tr>
              </tbody>
            </table>
            <p v-else class="empty-state">无隔离点</p>
          </div>
        </section>

        <section class="detail-panel">
          <h4>启停记录（{{ view.start_stop_events.length }}）</h4>
          <div class="scroll-box">
            <table class="data-table compact">
              <thead><tr><th>时刻</th><th>事件</th><th>说明</th></tr></thead>
              <tbody>
                <tr v-for="e in view.start_stop_events" :key="e.event_id">
                  <td>{{ e.at.slice(11) }}</td>
                  <td>{{ eventLabel(e.kind) }}</td>
                  <td>{{ String(e.detail.note ?? e.detail.at ?? '') }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      </div>

      <!-- 待复核尖峰 -->
      <h3 class="section-title">本步送往待复核的尖峰（{{ view.pending_spike_count }}）</h3>
      <table v-if="view.pending_spikes.length" class="data-table">
        <thead>
          <tr>
            <th>尖峰编号</th><th>采样时刻</th><th>观测值</th>
            <th>采用基线</th><th>基线均值</th><th>阈值增量</th><th>实际偏离</th><th>处置</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in view.pending_spikes" :key="s.spike_id" class="row-spike">
            <td>{{ s.spike_id }}</td>
            <td>{{ s.timestamp }}</td>
            <td>{{ s.value }}</td>
            <td><code>{{ s.baseline_id }}</code></td>
            <td>{{ s.baseline_mean }}</td>
            <td>{{ s.threshold_delta }}</td>
            <td>{{ s.actual_delta }}</td>
            <td>{{ s.disposition }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="empty-state">尚未提交正式复验，暂无待复核尖峰</p>

      <!-- 操作区 -->
      <div class="action-panel">
        <div class="action-group">
          <label>
            复核员
            <input v-model="reviewer" placeholder="如：复核员乙" />
          </label>
          <label class="grow">
            计算依据说明
            <input v-model="basisNote" placeholder="如：补入 3 个迟到平稳值后重算前段基线" />
          </label>
          <button type="button" class="btn" :disabled="loading || view.window.status === '已确认'" @click="onCompute">
            追加一版基线计算
          </button>
        </div>
        <div class="action-group">
          <label>
            批准人
            <input v-model="approver" placeholder="如：质检负责人" />
          </label>
          <button
            v-for="b in approvableBaselines"
            :key="b.baseline_id"
            type="button"
            class="btn"
            :disabled="loading"
            @click="onApprove(b.baseline_id)"
          >
            批准基线 {{ b.baseline_id }}
          </button>
          <button type="button" class="btn primary" :disabled="loading || !view.approved_baseline_id" @click="onSubmit">
            用获准基线完成本段正式复验
          </button>
        </div>
      </div>
    </div>

    <footer class="page-foot">
      <span>复验产物（窗口/基线/观测/事件/待复核尖峰）均落盘到 data/spike_review，可复现可审计</span>
      <span v-if="message" class="error-text">{{ message }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  approveBaseline,
  computeBaseline,
  fetchComparison,
  fetchWindows,
  initScenario,
  submitReview,
  type BaselineRow,
  type ComparisonView,
  type WindowRow,
} from '@/api/spike-review'
import WaveformCompare from '@/views/WaveformCompare.vue'

const windows = ref<WindowRow[]>([])
const selectedKey = ref('')
const view = ref<ComparisonView | null>(null)
const loading = ref(false)
const message = ref('')
const messageKind = ref<'ok' | 'err'>('ok')
const reviewer = ref('复核员乙')
const basisNote = ref('')
const approver = ref('质检负责人')

const adoptedBaseline = computed<BaselineRow | null>(() => {
  if (!view.value || !view.value.approved_baseline_id) return null
  return view.value.baselines.find((b) => b.baseline_id === view.value?.approved_baseline_id) ?? null
})

const approvableBaselines = computed(() =>
  (view.value?.baselines ?? []).filter((b) => b.status === '待批准'),
)

function flash(text: string, kind: 'ok' | 'err' = 'ok') {
  message.value = text
  messageKind.value = kind
}

async function loadWindows() {
  const payload = await fetchWindows()
  windows.value = payload.items
  if (!selectedKey.value && payload.items.length) {
    selectedKey.value = payload.items[0].window_key
  }
}

async function loadView() {
  if (!selectedKey.value) return
  view.value = await fetchComparison(selectedKey.value)
}

async function selectWindow(key: string) {
  selectedKey.value = key
  message.value = ''
  await loadView()
}

async function resetScenario() {
  loading.value = true
  try {
    await initScenario()
    await loadWindows()
    selectedKey.value = windows.value[0]?.window_key ?? ''
    await loadView()
    flash('标准复验场景已重置：后段保持已确认，前段两份依据可查')
  } catch (error) {
    flash(error instanceof Error ? error.message : '场景重置失败', 'err')
  } finally {
    loading.value = false
  }
}

async function onCompute() {
  if (!selectedKey.value) return
  loading.value = true
  try {
    await computeBaseline(selectedKey.value, reviewer.value, basisNote.value || '手动追加基线计算')
    await loadView()
    flash('已追加一版基线计算快照（旧版本保留可查，未获准前不影响正式复验）')
  } catch (error) {
    flash(error instanceof Error ? error.message : '基线计算失败', 'err')
  } finally {
    loading.value = false
  }
}

async function onApprove(baselineId: string) {
  if (!selectedKey.value) return
  loading.value = true
  try {
    await approveBaseline(selectedKey.value, baselineId, approver.value)
    await loadView()
    flash(`基线 ${baselineId} 已获准并冻结，同窗口其它待批准版本不得混用`)
  } catch (error) {
    flash(error instanceof Error ? error.message : '基线批准失败', 'err')
  } finally {
    loading.value = false
  }
}

async function onSubmit() {
  if (!selectedKey.value) return
  loading.value = true
  try {
    const result = await submitReview(selectedKey.value)
    await loadView()
    flash(
      `正式复验完成：采用基线 ${result.adopted_baseline_id}，本步送往待复核尖峰 ${result.pending_spike_count} 个`,
    )
  } catch (error) {
    flash(error instanceof Error ? error.message : '正式复验失败', 'err')
  } finally {
    loading.value = false
  }
}

function sourceLabel(source: string): string {
  if (source === 'late_arrival') return '迟到补入'
  if (source === 'isolated') return '隔离点'
  return '原始观测'
}

function eventLabel(kind: string): string {
  const map: Record<string, string> = {
    equipment_ramp_up: '设备升速',
    window_registered: '窗口登记',
    observations_loaded: '观测装载',
    baseline_computed: '基线计算',
    baseline_approved: '基线获准',
    baseline_rejected: '基线拒绝',
    review_submitted: '复验提交',
    window_confirmed: '窗口确认',
  }
  return map[kind] ?? kind
}

onMounted(async () => {
  try {
    await initScenario()
    await loadWindows()
    await loadView()
  } catch (error) {
    flash(error instanceof Error ? error.message : '复验场景初始化失败', 'err')
  }
})
</script>

<style scoped>
.status-banner {
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 13px;
  margin: 8px 0;
}
.status-banner.ok {
  background: #ecfdf3;
  color: #027a48;
  border: 1px solid #abefc6;
}
.status-banner.err {
  background: #fef3f2;
  color: #b42318;
  border: 1px solid #fda29b;
}
.window-tabs {
  display: flex;
  gap: 12px;
  margin: 12px 0;
}
.window-tab {
  flex: 1;
  text-align: left;
  border: 1px solid var(--border);
  background: #fff;
  border-radius: 8px;
  padding: 10px 14px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.window-tab.active {
  border-color: var(--brand);
  box-shadow: 0 0 0 2px rgb(31 111 235 / 18%);
}
.tab-meta {
  font-size: 12px;
  color: var(--muted);
}
.section-title {
  margin: 18px 0 8px;
  font-size: 15px;
}
.stat-value.small {
  font-size: 14px;
}
.stat-value.ok {
  color: #027a48;
}
.stat-value.warn {
  color: #b42318;
}
.status-pill {
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
  background: #f1f5f9;
}
.status-pill[data-status='已获准'] {
  background: #ecfdf3;
  color: #027a48;
}
.status-pill[data-status='已拒绝'] {
  background: #fef3f2;
  color: #b42318;
}
.status-pill[data-status='待批准'] {
  background: #fffaeb;
  color: #b54708;
}
.row-approved {
  background: #f8fffb;
}
.row-isolated td {
  color: #b42318;
}
.row-spike {
  background: #fffaeb;
}
.fingerprint {
  font-size: 11px;
  color: var(--muted);
}
.muted {
  color: var(--muted);
  font-size: 12px;
}
.warn {
  color: #b54708;
  font-size: 12px;
}
.detail-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  margin-top: 12px;
}
.detail-panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px;
}
.detail-panel h4 {
  margin: 0 0 8px;
  font-size: 13px;
}
.scroll-box {
  max-height: 260px;
  overflow: auto;
}
.data-table.compact th,
.data-table.compact td {
  padding: 5px 7px;
  font-size: 12px;
}
.action-panel {
  margin-top: 16px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.action-group {
  display: flex;
  gap: 10px;
  align-items: flex-end;
  flex-wrap: wrap;
}
.action-group label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--muted);
}
.action-group label.grow {
  flex: 1;
  min-width: 260px;
}
.action-group input {
  padding: 7px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 13px;
  min-width: 140px;
}
.btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
</style>
