# 01-feedstitch（信汇）

Feedstitch — 多源目录/价目 Feed 归并落库：解析 → 业务键去重/冲突隔离 → 按源水位线增量或全量落库。

## 启动

```bash
docker compose up --build
```

| 入口 | 地址 |
| --- | --- |
| 前端 | http://localhost:5100 |
| API | http://localhost:10100 |

## 主链

配置源与夹具 → 触发 Run → 解析 / 去重 / 冲突隔离 → Canonical + 运行报告对账

## 种子说明

- **Alpha Catalog（clean）**：JSON，三件干净货号，增量可落库。
- **Beta Offers（dirty）**：JSONL，含与 Alpha 同键但价格不同的冲突行，以及迟到低 revision 行（底座增量会因水位线跳过）。
- **Gamma Shelf（xml）**：简易 XML，两件独立货号。

打开「运行台」选源跑一次增量，再看 Staging / 冲突 / Canonical 三页计数是否与本次 Run 报告一致。

## 裁决规则

- 增量：`event_ts > watermark` 才进入常规裁决；迟到记录若落在
  `[watermark - lookback_seconds, watermark]` 且 `source_revision` 高于 canonical
  同键现有 revision，则允许回看 upsert（`lookback_upsert`）。窗口外、revision
  不够新、或 canonical 尚无该键的迟到记录仍记为跳过（全量重放会补上）。
- 冲突：同业务键的 `title / price / category` 与 canonical 分歧时写入冲突队列
  （`quarantine`），同时展示 existing / incoming payload；冲突行不会进入 canonical。
- 全量：先重置该源水位线，再按同一套裁决重放夹具；此前 quarantine 的冲突若仍
  分歧，会继续产生冲突记录，不会偷偷写进 canonical。
- 对账：Run 报告的 parsed / upserted / conflict / skipped 直接由该 run 的 staging
  disposition 汇总，`canonical_count` 在 run 结束时从 canonical 表统计。

## 技术栈

Python 3.12 + FastAPI + SQLite；Vue 3 + Vite + Nginx。

## 尚未实现

`dead_letter_replay` / `schema_drift_guard` / `webhook_push_source`
