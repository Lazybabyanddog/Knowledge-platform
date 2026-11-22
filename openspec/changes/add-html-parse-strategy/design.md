# Design

## Context

见 `proposal.md` 的问题描述。实现相关现状：

- `backend/app/domain/files.py` 已把 `.html` 映射为 `doc_type="html"` 并纳入白名单，上传侧无需改动。
- `backend/app/parsing/text_blocks.py::detect_doc_type` 只区分 pdf / office / image，其余一律 `text`，HTML 因此落在 `text`。
- `backend/app/pipelines/parse/graph.py` 有四个引擎节点（`layout_parse`/`plain_parse`/`office_parse`/`ocr_parse`），`routers.ROUTE_BY_TYPE` 决定分派；`make_engine_node(fallback)` 在策略引擎不支持当前类型时回落到传入的 fallback。
- 引擎契约（`app/parsing/base.py`）是纯函数：`ParseInput(data, file_name, mime_type, doc_type, config)` → `EngineResult(blocks, warnings, stats, needs_ocr)`，产物在 `normalize` 节点统一去空、去重、重排 `seq`、建立“块 → 最近标题”父子关系。
- `score_quality` 用 `stats.page_count` 计算每页字符数，缺省按 1 页处理。
- 策略 `doc_type` 在数据库里是字符串列，`DocType` 与审批流 `DocTypeLiteral` 都是 Pydantic 层面的 `Literal`，新增类型无 DB 迁移。
- 当前环境未安装任何 HTML 解析库，`pyproject.toml` 也无相关依赖。

样本（最高法公报案例页）的结构：正文容器 `#gb_content`，标题是 3 个 `p.c_title`，正文 38 个 `p.c_content`，案号为 1 个 `p.c_c_content`；导航、logo、6 个 `script`、2 个 `style` 都在容器外。

## Goals / Non-Goals

**Goals:**

- HTML 作为独立文档类型进入现有“类型 → 策略 → 引擎 → 内容块”链路，不引入第二套解析框架。
- 正文范围、标题判定、噪声剔除可通过策略配置，对同类站点可复用。
- 解析结果可解释：选择器失效、正文为空等情况留痕，不以“成功且高质量”掩盖问题。

**Non-Goals:**

- 不执行 JavaScript、不渲染 SPA 动态内容；只解析静态 HTML 文本。
- 不做 URL 抓取，仍以已上传文件为输入。
- 不自动推断正文（不做 readability 类通用算法），正文范围由选择器显式给出。
- 不把 HTML 全文转 Markdown 作为对外产物，只产出系统既有的内容块。
- 不扩展审批流文档类型（仍为 `pdf`/`office`/`image`/`text`）。

## Decisions

### D1：新增独立 `html` 文档类型（而非在纯文本引擎内嗅探）

`detect_doc_type` 增加 `html` 分支，`FileType`、`DocType`、前端 `DOC_TYPE` 同步；策略可按 `html` 单独配置默认策略。

- 备选：在 `plain_text` 引擎里检测 `<html` 后转交 HTML 逻辑。改动最小，但策略无法按类型配置、质量统计含糊、行为隐式。
- 备选：引入 `auto` 引擎按扩展名分派。更通用，但需要迁移全部四个既有策略，超出本次范围。

代价是 5 处枚举/文案同步，收益是路由与统计保持一致，且后续 `htm`、站点模板扩展有落点。

### D2：用 BeautifulSoup + lxml 解析 DOM

- `bs4` 提供树遍历与 CSS 选择器，`lxml` 提供容错 HTML 解析（自动补全未闭合标签），这类页面常见 `p` 嵌套不规范，标准库 `html.parser` 需要自行处理树构建与容错，代码量与边界风险更高。
- 解析器按 `lxml` → `html.parser` 顺序尝试，缺少 lxml 二进制时仍可运行（bs4 内置解析器），避免部署环境编译失败直接阻断解析。
- lxml 以 HTML 模式解析，不加载外部实体，不访问网络；`script`/`style` 在进入映射前即移除，不参与文本拼接。

### D3：正文范围由 `content_selector` 显式指定，默认 `body`

可预测、可复现、可用固定 fixture 断言。自动正文抽取（readability/trafilatura）对“结构固定、selector 已知”的公报/制度页属于过度设计，且结果不可解释。

未命中时回退 `body` 并写 warning，而不是直接失败：同一策略可能服务多个页面模板，硬失败会让可解析的页面也失败。

### D4：新增 `html_parse` 图节点，并把该分支的兜底引擎设为 HTML 引擎

`ROUTE_BY_TYPE["html"] = "html_parse"`，`graph.py` 增加 `html_parse = make_engine_node("html_dom")`，`ROUTE_TARGETS` 同步。

这样处理两个方向：

- 策略指定 `html_dom` → 直接使用；
- 策略仍指向 `plain_text`（例如管理员手工建的通用策略）→ `plain_text.supports("html")` 为 false，节点回落到 `html_dom`，HTML 文档永远不会退回按行切源码。

同时 `route_needs_ocr` 只在 `doc_type=pdf` 的版面节点之后才可能触发；`html_parse` 直接连到 `table_extract`，天然不进入 OCR 分支。

### D5：块映射与标题判定优先级

单个节点按以下优先级判定（先命中先定）：

1. `title_selector` 命中 → 标题
2. 标签为 `h1`–`h6` → 标题，`level` 取标签数字
3. 无块级子节点（即叶子）且 `class`/`id` 含 `title` → 标题，`level=1`
4. 标签为 `p`、`div`、`section`、`article`、`blockquote` 且含文本 → 段落
5. `li` → 列表；`table` → 表格；`pre`/`code` → 代码；`img` → 图片（受 `keep_images` 控制）
6. `header`/`footer`/`nav`/`aside` → 页眉页脚块，交由 `normalize` 按 `remove_header` 丢弃

第 3 条限定“叶子节点”，避免把 `div.title-wrapper` 这类容器整块吞成标题；容器本身仍会继续遍历其子节点。

标题合并（`merge_titles`，默认 true）：对相邻标题块，若前一块文本不以 `。！？；.!?;` 结尾，则与后一块以空格拼接。样本中 3 个 `c_title` 会合成 1 个标题，案号（`c_c_content`）保持段落，符合“标题 + 案号 + 正文”的文书结构。

### D6：噪声剔除

固定移除：`script`、`style`、`noscript`、`template`、`iframe`、`svg`、`form`、`input`、`button`。

策略可追加 `drop_selectors`（列表），用于站点特定的面包屑、分享栏、上一篇/下一篇。

图片默认不保留（`keep_images=false`），因为网页装饰图（logo、图标、二维码）对抽取无价值，且会拉低质量评分中“图片占比”这一项。

### D7：无分页语义

HTML 无分页，所有块 `page_no=1`、`bbox=None`、`stats.page_count=1`。这样质量评分沿用现有公式，不引入新分支；`chunk_hint` 仍由后续切分使用。

### D8：编码

复用 `decode_text`（`utf-8-sig` → `utf-8` → `gb18030` → 替换解码）。不读取 `<meta charset>` 作为解码依据：声明与实际不一致是常见情况（样本引用 `charset="gbk"` 的外部脚本，但文件本身是 UTF-8），按字节解码更可靠。

### D9：表格渲染沿用下游约定

表格块内容渲染为 `| cell | cell |` 的等宽文本，`table_extract` 节点已按此格式解析、合并同页碎片并重渲染。嵌套表格只取最外层，内层单元格文本并入外层单元格，避免块数爆炸。

### D10：策略配置项与默认值

| 配置项 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `content_selector` | string | `body` | 正文容器 CSS 选择器 |
| `title_selector` | string | 空 | 额外标题选择器 |
| `merge_titles` | bool | `true` | 相邻标题合并 |
| `drop_selectors` | string[] | `[]` | 追加剔除的节点 |
| `keep_images` | bool | `false` | 是否产出图片块 |
| `remove_header` | bool | `true` | 沿用现有开关 |

配置语义校验只在解析时发生：非法选择器抛 `ParseError`，任务失败并记录原因；不在创建策略时做 CSS 语法校验，避免为一个可重试的运行时错误引入额外校验层。

## Risks / Trade-offs

- **选择器与站点强耦合** → 选择器失效时回退 `body` 并写 warning，任务可重解析；策略可复制后按站点调整。
- **新增依赖安装失败（lxml 无预编译包）** → 解析器按 `lxml` → `html.parser` 回退，纯 Python 路径仍可用；`pyproject.toml` 固定版本下限。
- **BREAKING：存量 HTML 文档在 seed 前触发解析会 400** → 部署顺序为先跑 seed 再切换路由；`seed` 对 `strategy_key` 幂等，可重复执行。
- **标题误判** → 限定叶子节点 + 句末标点规则；样本与新增单测覆盖“容器不该被当标题”。
- **重复段落被 `normalize` 去重** → HTML 中真实重复段落（如重复条款）可能被误删；这是既有全局行为，本次不改，记录为已知限制。
- **大文档性能** → 单文件 200MB 上限已在；DOM 只遍历一次，输出块数受 `extract_max_blocks` 后续约束，本次不额外设限。

## Migration Plan

1. 安装依赖 `beautifulsoup4`、`lxml`。
2. 落代码：枚举与 schema → 新引擎 → 路由节点 → seed → 前端文案与引擎候选。
3. 执行 `seed`，写入 `html` 默认策略（`strategy_key = html-default`）。
4. 对存量 HTML 文档手动触发重解析，核对块数与噪声比例。
5. 回滚：移除 `html` 路由与引擎，`detect_doc_type` 恢复归入 `text`；`doc_type="html"` 的策略行保留不影响其他类型，无需数据回滚。

## 已决范围

- `.htm` 扩展名不在本次范围：上传白名单仍只有 `.html`，避免同时改动上传校验与 `_FILE_TYPES` 映射；如需支持另开变更。
