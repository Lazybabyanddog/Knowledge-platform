# Proposal

## Why

`.html` 已在后台上传白名单内，但 `detect_doc_type` 把 HTML 归入 `text`，由 `plain_text` 引擎按行切分源码。用 `docs/` 下的最高法公报案例页实测：41 个内容块中 31 个含 HTML 标签或脚本（约 76%），真正正文 8,216 字被 `<script>`、CSS、导航淹没，抽取阶段拿到的几乎是废料。

HTML 是制度页面、法院公报、公告等常见知识来源，需要按文档结构（标题、段落、列表、表格）产出内容块，而不是当纯文本读。

## What Changes

- 新增独立文档类型 `html`：`detect_doc_type` 不再把 `.html` 归入 `text`。**BREAKING**：存量 HTML 文档的解析依赖新建的 html 默认策略，缺失时触发解析返回 400。
- 新增解析引擎 `html_dom`：基于 DOM 遍历把 HTML 转成内容块，默认丢弃 `script`/`style`/`noscript`，正文容器可用 `content_selector` 指定。
- 新增 HTML 策略配置项：`content_selector`、`title_selector`、`merge_titles`、`drop_selectors`、`keep_images`。
- 路由新增 `html` → `html_parse` 分支；该分支内引擎缺失或 selector 失效时明确失败或告警，**不回退纯文本引擎**。
- 种子数据新增 HTML 默认策略；前端补充 HTML 类型文案与 `html_dom` 引擎候选。
- 新增依赖 `beautifulsoup4`、`lxml`。

## Capabilities

### New Capabilities

- `html-parsing`: HTML 文档的类型识别、策略路由与结构化解析产物（正文定位、块映射、噪声剔除、无分页语义）。

### Modified Capabilities

（无。`openspec/specs/` 目前为空，`knowledge-base-platform` 的 `parsing-pipeline` delta 尚未同步为主规格，本次不修改该能力，避免与其归档冲突。）

## Impact

- 后端：`app/domain/enums.py`、`app/schemas/parse.py`、`app/parsing/text_blocks.py`、`app/parsing/engines/html_dom.py`（新增）、`app/parsing/engines/__init__.py`、`app/pipelines/parse/routers.py`、`app/pipelines/parse/graph.py`、`app/scripts/seed.py`、`pyproject.toml`
- 前端：`src/utils/format.ts`（`DOC_TYPE`）、`src/views/ParseStrategiesView.vue`（引擎候选）
- 数据：策略 `doc_type` 为字符串列，无需迁移；需重跑 seed 写入 `html` 默认策略
- 依赖：`beautifulsoup4`、`lxml`
- 不在范围：审批流类型仍为 `pdf`/`office`/`image`/`text`，HTML 文档提交审批与现有 `txt`/`md` 行为一致（本就不在四类内），另行处理
