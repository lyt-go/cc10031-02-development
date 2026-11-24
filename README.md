# 工业传感器时序采集与降采样平台

面向工业传感器高频时序数据的测点接入、边缘缓冲上送、单位标定、时间对齐、降采样归并与分层归档的采集平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/views/SpikeReviewView.vue    尖峰分段复验与波形对照视图
│   ├── src/views/WaveformCompare.vue    参与观测/隔离点/阈值带 SVG 波形
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/routers/spike_review.py  尖峰分段复验专用路由
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/spike_review.py           窗口/基线快照/隔离点/复验
│   ├── app/services/spike_review_scenario.py  升速前后标准业务场景
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 测点台账 | `sensor_point` | 测点档案 | 测点编号、所属产线、传感器型号 |
| 边缘缓冲 | `edge_buffer` | 缓冲分片 | 分片编号、测点编号、起始时刻 |
| 批量上送 | `upload_batch` | 上送批次 | 批次编号、分片范围、测点数量 |
| 单位标定 | `unit_calibrate` | 标定记录 | 标定编号、测点编号、原始单位 |
| 时间对齐 | `time_align` | 对齐规则 | 规则编号、对齐粒度、时区基准 |
| 断点填补 | `gap_fill` | 填补区间 | 区间编号、测点编号、缺失起点 |
| 尖峰剔除 | `spike_filter` | 异常采样 | 异常编号、测点编号、采样时刻 |
| 降采样 | `downsample` | 降采样任务 | 任务编号、源序列、源采样频率 |
| 窗口归并 | `rollup_window` | 归并窗口 | 窗口编号、测点编号、窗口类型 |
| 序列索引 | `series_index` | 索引分区 | 索引编号、测点编号、时间分区 |
| 冷热分层 | `tier_storage` | 分层策略 | 策略编号、时间边界、热区范围 |
| 采集心跳 | `device_health` | 心跳记录 | 心跳编号、采集终端、上报时刻 |
| 接入水位 | `ingest_watermark` | 水位标记 | 水位编号、测点编号、事件时间水位 |
| 区间重采 | `range_replay` | 重采任务 | 重采编号、测点编号、起始时刻 |
| 采集血缘 | `series_lineage` | 血缘链路 | 链路编号、测点编号、上送批次 |
| 序列导出 | `series_export` | 导出任务 | 导出编号、测点范围、时间范围 |
| 时序质量 | `series_quality` | 质量报告 | 报告编号、测点编号、统计窗口 |
| 序列快照 | `series_snapshot` | 版本快照 | 快照编号、口径说明、测点范围 |

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 尖峰分段复验与波形对照

针对「设备升速前后各有短暂高值、旧班已确认后段为生产数据、迟到平稳值拉低前段基线」的场景，
尖峰剔除（`spike_filter`）与时序质量（`series_quality`）提供逐段复核能力，后端在
`app/services/spike_review.py`、路由在 `app/routers/spike_review.py`（前缀 `/api/spike-review`），
前端入口为导航「尖峰复验对照」（`/spike-review`）。

核心规则：

- **窗口与有效窗**：复验以窗口为单位（如升速前 `PRE`、升速后 `POST`），每窗锁定独立时间区间与阈值口径。
- **基线冻结**：已确认窗口的基线一经获准即冻结；之后到达的数据只登记为**隔离点**，
  绝不参与原窗口阈值计算（不把后到数据混作原基线），冻结窗口也拒绝再计算新基线。
- **多版本可查、单基线获准**：同一窗口可保留多份基线快照（含参与观测、数据指纹、隔离点、启停事件），
  但一次正式复验只允许一条「已获准」基线；提交时若指定其它基线直接拒绝，不能混合两个基线。
- **可复现**：每版基线对参与计算的连续观测生成 SHA-256 指纹；正式复验前校验指纹，不一致即拒绝。
- **对照视图**：`GET /api/spike-review/comparison/{window_key}` 逐段返回实际参与阈值计算的连续观测、
  被隔离的点、启停记录、基线版本与待复核尖峰；前端 SVG 波形同步绘制阈值带、均值线与隔离标记。
- **落盘审计**：窗口、基线、观测、事件、待复核尖峰分别写入 `data/spike_review/` 下的 JSON/JSONL 文件。

标准场景可通过 `POST /api/spike-review/scenario/init` 一键装载（幂等重置），
集成测试见 `backend/tests/test_spike_review.py`。

领域：数据工程
子方向：数据采集、爬虫与RPA
