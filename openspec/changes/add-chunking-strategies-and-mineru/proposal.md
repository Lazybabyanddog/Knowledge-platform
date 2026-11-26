# Proposal

## Why

PDF 解析目前只有基于 pypdf 文字层的 `pdf_layout` 引擎：扫描件、双栏排版、公式与复杂表格只能落到 OCR 兜底，结构化程度不足，检索拿到的常是错行文本。同时切块只有一个算法和一个 `chunk_hint` 数字，无法按文档形态选择切法——长篇制度文件与短问答材料的理想切法并不相同。

## What Changes

- 新增解析引擎 `mineru`（仅 PDF）：调用本机部署的 MinerU 服务，把版面级产物（标题层级、段落、表格、公式、图片、页码）映射为现有内容块模型。策略里把 `engine` 设为 `mineru` 即启用，无需新文档类型。
- 新增 MinerU 连接配置：服务地址、单次调用超时、语言，以及策略级的表格/公式开关。
- MinerU 失败语义分流：服务不可达或调用超时归为可重试错误码（新增 `mineru_unavailable`），产物不合法归为不可重试的 `parse_error`。**不做自动降级到 `pdf_layout`**，避免用户在不知情的情况下拿到质量差异巨大的结果。
- 新增 `parse.mineru` 队列：`engine=mineru` 的解析任务投递到独立队列，由独占 GPU 的 worker 消费。现状是所有任务共用 `parse` 队列且 worker 为 `--pool=solo`，一次分钟级的 MinerU 解析会连 `watchdog.sweep` 一起堵住。
- 切块策略可选：`ParseStrategy.config.chunk = {strategy, size, overlap}`，`strategy` 取 `block_merge`（默认，现行行为）、`fixed_window`、`heading_section`。本期三种均落在现有块产物上，**零数据库迁移**。
- 切块配置向后兼容：`config.chunk.size` 缺省时回退到既有的 `config.chunk_hint`，再回退到 800；存量策略无需改动。
- 新增 `GET /parse-strategies/engines`，前端引擎候选改为从注册表读取。现状前端候选写死且已与后端漂移（`pdfminer`/`python_docx`/`ocr_tesseract` 均不存在）。
- 前端解析策略表单支持选择切块策略与参数，不再要求手写 JSON。

## Capabilities

### New Capabilities

- `mineru-parsing`: 本地 MinerU 引擎的接入契约——引擎注册与路由、产物到内容块的映射、超时与错误分类、独立队列与不降级语义。
- `chunking-strategy`: 切块策略作为解析策略的一部分——可选策略的配置与校验、由策略版本驱动的生效规则、缺省与向后兼容。

### Modified Capabilities

（无。`openspec/specs/` 尚无 `parsing-pipeline` 主规格，`knowledge-base-platform` 的 delta 未同步；沿用 `add-html-parse-strategy` 的先例，本次不改动该能力，避免归档冲突。）

## Impact

- 后端：`app/parsing/engines/mineru.py`（新增）、`app/parsing/engines/__init__.py`、`app/parsing/base.py`、`app/core/config.py`、`app/workers/celery_app.py`、`app/workers/tasks.py`、`app/services/parse_task_service.py`、`app/services/task_watchdog.py`、`app/services/chunking.py`（拆分 + 注册表）、`app/services/index_service.py`、`app/schemas/parse.py`、`app/api/v1/parse.py`、`app/scripts/seed.py`
- 前端：`src/views/ParseStrategiesView.vue`、`src/api/parse.ts`、`src/api/types.ts`
- 部署：需在本机安装并常驻 MinerU 服务；`scripts/start-worker.ps1`、`docker-compose.release.yml` 增加 `parse.mineru` 队列的 worker
- 依赖：`httpx`（已在依赖清单中），无新增 Python 包
- 数据：无迁移。切块配置随解析策略版本化，片段可经 `parse_task.strategy_id` 追溯当时的切块配置
- 假设：MinerU 以**本机独立服务**形态部署（`mineru-api`，默认 `http://127.0.0.1:8000`），后端通过 HTTP 调用，而非在 worker 进程内 import SDK
- 不在范围：`parent_child` 切块（二期，需给 `document_chunk` 增加父子列，本变更仅在 `tasks.md` 与 `design.md` 登记方案）；MinerU 云端 API；进展百分比式进度上报；`doc_type` 新增
