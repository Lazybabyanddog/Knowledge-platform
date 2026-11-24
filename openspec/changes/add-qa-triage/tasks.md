# Tasks

## 1. 数据模型与迁移

- [x] 1.1 新增 `QaRecord`、`QaFeedback`、`QaIssue`、`QaIssueRecord`、`QaIssueEvent` 五个模型并在 `infra/db/models/__init__.py` 登记，验证：`python -c "from app.infra.db.models import QaRecord, QaIssue"` 通过，且 `alembic revision --autogenerate` 能识别这五张表
- [x] 1.2 编写迁移建表与索引（`question_vec` / `representative_vec` 的 HNSW、`reasons` 的 GIN、`qa_issue_record.record_id` 唯一、反馈 `(record_id, user_id)` 唯一），验证：`alembic upgrade head` 后表结构与索引齐备，`alembic downgrade -1` 可回退
- [x] 1.3 在同一迁移内为 `role.code='admin'` 补 `qa:manage`（`ON CONFLICT DO NOTHING`），验证：升级后查询 admin 角色权限含该编码，重复执行迁移不报错

## 2. 权限与可见性基础

- [x] 2.1 新增 `Permission.QA_MANAGE` 与 `PERMISSION_CATALOG` 条目，验证：`/roles` 返回的能力目录含"问答治理"，admin 角色权限集合包含 `qa:manage`
- [x] 2.2 把 `allowed_sensitivities` 提到 `app/domain/visibility.py` 并让 `services/retrieval_service.py` 改为导入，验证：`pytest tests/integration/test_retrieval.py -q` 全绿，检索行为无变化
- [x] 2.3 前端 `constants.ts` 增加 `qa:manage` 类型与中文标签，验证：角色管理页权限分组出现"问答治理"

## 3. 问答留痕与反馈采集

- [x] 3.1 在 `ask` 的流开始前创建 `streaming` 记录并在 `done` 事件回传记录标识（无命中的直接结束同样携带），验证：`tests/integration/test_retrieval.py` 新增断言覆盖两种结束方式的 `qa_record_id`
- [x] 3.2 用 `session_scope()` 在流结束后补写答案、耗时与终态，验证：正常、无命中、中途断开、模型失败四种情形分别落为 `completed` / `completed`（空来源）/ `interrupted` / `failed`，且后两者不含可反馈标识
- [x] 3.3 新增配置（`qa_triage_enabled`、`qa_record_retention_days`、`qa_low_hit_floor`、`qa_score_mark_threshold`、`qa_score_gap_threshold`、`qa_cluster_similarity`），验证：`settings` 可读默认值，`qa_triage_enabled=false` 时只落库不判定
- [x] 3.4 实现反馈接口 `PUT/GET /qa-records/{id}/feedback`（仅本人、重复提交覆盖、不认可必须带原因、"其他"必填说明），验证：`tests/integration/test_qa_triage.py` 覆盖四个场景，含他人记录返回未找到

## 4. 判定与问题簇

- [x] 4.1 实现 `evaluate` 计算自动原因（`no_hit` / `no_citation` 为硬信号，`few_hits` / `low_score` / `score_gap` 只打标），验证：单测覆盖"仅软信号不进队列""无引用硬触发"两类输入
- [x] 4.2 实现硬信号记录转待处置与归簇（先精确条件、再代表向量匹配，限定同空间同密级、关闭簇不参与），验证：单测断言同义问题合并计数、不同密级各自成簇、已关闭簇下新记录另起新簇
- [x] 4.3 在反馈提交时把"不认可"并入原因并触发入队，验证：先正常判定后被点踩的记录进入队列，且排序优先于只有自动信号的记录；改为认可后队列排序随之调整
- [x] 4.4 提供 `QaTriageService.reevaluate(workspace_id)` 重算自动原因（保留用户原因），验证：下调标记线后既有记录打标变化，`user_dislike` 不被覆盖

## 5. 治理端接口

- [x] 5.1 实现 `GET /qa-issues` 与 `GET /qa-issues/{id}`（按状态、结论、原因、时间、处理人、文档筛选；影响面排序；详情含成员记录、来源文档与轨迹），验证：跨空间与超密级数据不出现在列表、详情与计数中，访问不可见簇返回未找到
- [x] 5.2 实现 `assign` / `dispose` / `close` / `reopen` 四个动作端点与只追加轨迹，验证：缺结论的关闭返回 400，重开后轨迹保留历史，四个动作各有对应审计事件

## 6. 前端交互

- [x] 6.1 `AskView` 增加反馈条（认可/不认可、原因选择、已反馈态、修改评价入口），验证：点踩后治理端队列出现该记录，刷新后能读回已提交评价
- [x] 6.2 新增 `QaTriageView`、`api/qa.ts` 与路由（`qa:manage` 守卫），验证：无权限用户菜单不可见且直达跳 403；有权限时可筛选、查看成员记录、执行处置并看到轨迹与来源文档跳转

## 7. 集成验证

- [x] 7.1 编写端到端集成测试：提问 → 点踩 → 入队 → 归簇 → 关闭 → 审计可查，验证：`pytest tests/integration/test_qa_triage.py -q` 通过
- [x] 7.2 核验与 `add-rag-retrieval` 的审计要求不冲突（审计仍只记问题摘要与命中数），验证：`tests/integration/test_retrieval.py` 中审计断言保持通过
