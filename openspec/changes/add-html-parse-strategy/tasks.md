# Tasks

## 1. 依赖与类型基线

- [x] 1.1 在 `backend/pyproject.toml` 增加 `beautifulsoup4`、`lxml` 依赖并安装，验证 `python -c "import bs4, lxml; print(bs4.__version__)"` 输出成功
- [x] 1.2 在 `backend/app/domain/enums.py` 的 `FileType` 与 `backend/app/schemas/parse.py` 的 `DocType` 增加 `html`，并在 `frontend/src/utils/format.ts` 的 `DOC_TYPE` 增加 `html: 'HTML'`，验证 `pytest tests/unit -q` 与 `npm run type-check` 通过

## 2. HTML 解析引擎

- [x] 2.1 新增 `backend/app/parsing/engines/html_dom.py`：继承 `ParseEngine`，实现解码与解析器回退（`lxml` 不可用时用 `html.parser`）、`page_no=1`、`stats.page_count=1`，验证新增单测断言样本页产出的块数与 `page_count`
- [x] 2.2 实现正文范围与噪声剔除：`content_selector`（默认 `body`）、`drop_selectors`、固定移除 `script/style/noscript/template/iframe/svg/form/input/button`，选择器未命中时回退 `body` 并写 warning，非法选择器抛 `ParseError`，验证单测覆盖三种场景
- [x] 2.3 实现块类型映射：标题标签（级别 1–6）、段落、列表项（每项一块）、表格（保留行列并按 `|` 渲染）、代码、图片（受 `keep_images` 控制）、页眉页脚（交 `normalize` 按 `remove_header` 丢弃），验证单测断言各类块数量与内容
- [x] 2.4 实现标题识别与合并：`title_selector` 优先生效、无块级子节点且类名含 `title` 判为标题、`merge_titles` 默认合并相邻标题，验证单测：样本页 3 个 `c_title` 产出 1 个标题块，关闭开关后为 3 个
- [x] 2.5 在 `backend/app/parsing/engines/__init__.py` 注册新引擎，验证 `registry.get_engine("html_dom")` 可解析且 `supports("html")` 为真
- [x] 2.6 建立 `backend/tests/fixtures/html/` 测试样本（从 `docs/` 参考页提取结构、剔除外链资源）并完成 `backend/tests/unit/test_html_engine.py`，验证 `pytest tests/unit/test_html_engine.py -q` 全绿

## 3. 路由、策略与种子数据

- [x] 3.1 在 `backend/app/parsing/text_blocks.py::detect_doc_type` 增加 `html` 分支，验证单测断言 `.html` 返回 `html`、`.txt/.md/.csv` 仍返回 `text`
- [x] 3.2 在 `backend/app/pipelines/parse/routers.py` 增加 `html` → `html_parse`，在 `graph.py` 增加 `html_parse = make_engine_node("html_dom")` 与 `ROUTE_TARGETS`，验证：html 文档解析后 `stats.engine == "html_dom"`，且策略指定 `plain_text` 时仍落到 `html_dom`
- [x] 3.3 在 `backend/app/scripts/seed.py` 增加 html 默认策略（`strategy_key=html-default`、`engine=html_dom`、默认配置），验证重跑 seed 幂等且 `GET /api/v1/parse-strategies?doc_type=html` 返回该策略
- [x] 3.4 验证策略接口行为：创建 `doc_type=html` 策略成功并可作为默认、引用未注册引擎返回 400，验证 `pytest tests/integration/test_parse.py -q -k html` 覆盖新增用例

## 4. 前端

- [x] 4.1 `frontend/src/views/ParseStrategiesView.vue` 引擎候选数组加入 `html_dom`，验证页面可创建 `doc_type=html` 策略并保存成功
- [x] 4.2 确认文档列表与详情页对 `html` 类型显示「HTML」，验证 `npm run type-check` 与 `npm run build` 通过

## 5. 集成验证

- [x] 5.1 用 `docs/` 参考页跑端到端：上传 → 触发解析 → 查询内容块，验证块内容不含 HTML 标签或脚本、标题合并为 1 个、正文有效字符数不少于 8000、质量评分不低于阈值
- [x] 5.2 回归既有类型：`pytest tests/unit tests/integration/test_parse.py tests/integration/test_documents.py -q` 全绿，确认 txt/md/pdf/docx 路由与上传校验无变化
- [x] 5.3 在 `README.md` 补充 HTML 解析策略配置说明（正文选择器、标题选择器、默认值与重解析步骤），验证按文档描述可复现样本页的解析结果

## Workflow follow-up

- 变更实现并通过评审后执行 `/opsx:archive` 归档。
