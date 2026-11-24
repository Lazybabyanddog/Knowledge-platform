# Design

## Context

现有问答是一次性管道，没有落点：`RetrievalService.search` 返回带 `score` 的片段，`api/v1/retrieval.py` 在进入 SSE 流之前完成检索与审计并 `commit`，随后 `_event_stream` 只把 `sources`、`delta`、`done` 推给前端。答案正文只在生成器里流过一遍，不落库；审计表只写 `question[:100]` 与命中数。

三个既有约束直接决定实现形状：

- **流期间没有可用会话**。`get_db` 产出的 Session 随依赖退出而关闭，流内不能依赖它写库；`session.py` 已提供 `session_scope()`，适合在流结束后独立提交。
- **密级过滤只有一个来源**。`allowed_sensitivities(principal)` 目前定义在 `services/retrieval_service.py`，治理端需要同一套规则，不能再复制一份。
- **权限是运行时的**。`core/deps.py` 从 `role_permission` 表读取权限，`BUILTIN_ROLE_PERMISSIONS` 只用于种子数据；给既有库补新权限必须走迁移。

## Goals / Non-Goals

**Goals:**

- 一次问答在流结束后必然留下可复核证据，且写入不拖慢首字延迟
- 低置信判定可重算：原始信号与阈值解耦，改阈值不需要重新采集
- 治理端操作对象是"问题簇"，单条记录只是成员与证据
- 密级与空间隔离在治理端与检索端使用同一套判定

**Non-Goals:**

- 不做实时聚类任务队列，归簇在写入路径上同步完成
- 不做跨空间聚合视图（无平台角色，见 proposal Out of Scope 第 4 项）
- 不做清理作业，只留时间字段与配置（第 7 项）
- 不引入 LLM 裁判（第 8 项）

## Decisions

### D1 记录在流开始前建立、在流结束后补全

流开始前创建 `qa_record`（状态 `streaming`）并提交，拿到主键随 `done` 事件下发；流结束后用 `session_scope()` 补写答案、耗时与终态，再执行判定与入队。

- 为什么先建后补：`done` 事件必须带记录标识，而标识要先于答案存在；流中累积的 delta 只放内存缓冲，避免每个增量都写库。
- 为什么不复用请求 Session：流式响应期间依赖已退出，Session 可能已关闭；跨线程复用同一 Session 也不安全。
- 备选（否决）：流结束后一次性插入整条记录。这会让 `done` 事件无法携带标识，前端在流结束到记录落库之间无法提交反馈。

### D2 判定分两次，自动原因可重算，用户原因不动

`qa_record.reasons` 存两类原因：自动判定产出（`no_hit`、`few_hits`、`low_score`、`score_gap`、`no_citation`）与用户产出（`user_dislike`）。

- 自动原因由 `evaluate(record)` 计算，输入是落库的信号快照 + 当前阈值；重算只覆盖自动部分。
- `no_hit`、`no_citation`、`user_dislike` 为硬信号，命中任一即把记录转为待处置；`few_hits`、`low_score`、`score_gap` 只打标。
- 重算入口是服务方法而非界面：`QaTriageService.reevaluate(workspace_id)`，本期由运维脚本调用，界面留给 L3。

### D3 问题簇自带代表向量，两段匹配

新建 `qa_issue` 时把触发记录的 `question_vec` 存为代表向量，后续记录先查精确条件、再比向量：

```
新记录（同 workspace、同 sensitivity、completed）
   |
   +--> 1) 找一个 pending 簇：primary_document_id 相同 且 原因集合与之一致
   |        命中 --> 并入
   |
   +--> 2) 否则遍历 pending 簇，取 representative_vec 余弦相似度最大值
            >= qa_cluster_similarity --> 并入
            否则 --> 新建簇，自己成为代表
```

- 为什么不用"记录两两聚类"：需要 O(n²) 比较或额外任务调度，结果随写入顺序漂移；以簇为代表做增量归并，代价固定在"pending 簇数量"级别。
- 为什么同密级才归并：跨密级合并会让低密级治理者看到高密级簇的存在与计数，等于绕过 D6 的可见性过滤。
- 已关闭簇不参与匹配，新记录另起新簇（见 spec）。

### D4 数据模型

```
qa_record
  id, workspace_id, user_id
  question, question_vec(Vector 1024)
  answer(Text), answer_status(streaming|completed|interrupted|failed)
  sources(JSONB)          本次来源快照，含编号/文档/片段/标题/页号/分数/文档密级
  hit_count, top_score, last_score
  reasons(JSONB)          自动原因 + 用户原因，GIN 索引
  sensitivity             来源文档最高密级；未命中为 public
  elapsed_ms, started_at, finished_at, created_at

qa_feedback
  id, record_id, user_id, rating(like|dislike), reason, note
  created_at, updated_at
  UNIQUE(record_id, user_id)   一人一条，重复提交即覆盖

qa_issue
  id, workspace_id, sensitivity
  representative_question, representative_vec(Vector 1024)
  primary_document_id
  record_count, first_seen_at, last_seen_at
  status(pending|closed), disposition, assignee_id
  created_at, updated_at

qa_issue_record
  issue_id, record_id, linked_at
  UNIQUE(record_id)            一条记录只属于一个簇

qa_issue_event
  id, issue_id, action(assign|dispose|close|reopen), actor_id, note, created_at
```

- `workspace_id` 与 `sensitivity` 冗余在 `qa_record` 与 `qa_issue` 上：治理列表的主要过滤维度，避免每次 join 文档表。
- `sources` 存 JSONB 快照而不只存 chunk_id：文档后续被下架或改版后，证据仍需可读。
- `disposition` 与 `status` 分开两列：关闭必须带结论，但"误报"也是关闭，只有分开才能统计误报率（spec 要求）。

### D5 归并落在写入路径，用记录级唯一约束兜住重复

`qa_issue_record.record_id` 唯一，把"一条记录只能属于一个簇"交给数据库；并发下两个请求可能各建一个新簇，属于可接受的浪费（治理者可把空簇关为误报）。不在本期加 advisory lock。

### D6 治理端可见性复用检索端规则

把 `allowed_sensitivities` 从 `services/retrieval_service.py` 提到 `app/domain/visibility.py`，检索与治理两端都从该模块导入：

- 列表/详情 SQL 里同时下推 `workspace_id` 与 `sensitivity IN (...)`，与 `_base_stmt` 的做法一致——先过滤再计数，避免"先算总量再筛"泄露不可见集合规模。
- 对不可见簇的访问统一返回 `NotFoundError`，不返回 403，避免存在性泄露。

### D7 权限与迁移

- `Permission.QA_MANAGE = "qa:manage"`，加入 `PERMISSION_CATALOG`（分组"问答"，排序 150）；`_ADMIN = frozenset(Permission)` 自动包含，reviewer 不默认授予。
- 数据迁移 MUST 给既有 `role.code='admin'` 补一行 `role_permission`（`ON CONFLICT DO NOTHING`）。种子数据只影响新库，漏掉这步会让现网管理员看不到治理入口。
- 前端 `constants.ts` 增加 `qa:manage` 与中文标签，路由 `meta.permission` 用它守卫，导航由既有菜单逻辑自动显隐。

### D8 接口

```
POST /retrieval/ask                       done 事件新增 qa_record_id（无命中同样携带）
PUT  /qa-records/{id}/feedback            {rating, reason?, note?} 幂等覆盖，仅本人
GET  /qa-records/{id}/feedback            读回本人评价

GET  /qa-issues                           status/disposition/reason/assignee/document_id/from/to/分页
GET  /qa-issues/{id}                      含成员记录、来源文档、轨迹
POST /qa-issues/{id}/assign               {assignee_id}
POST /qa-issues/{id}/dispose              {disposition, note?}
POST /qa-issues/{id}/close                {disposition, note?}  缺结论则 400
POST /qa-issues/{id}/reopen               {note?}
```

采用显式动作端点而非一个 `PATCH`，与既有 `reviews` 的 `approve/reject/withdraw` 风格一致，也让每个动作都能对应一条轨迹与一条审计。

### D9 前端

- `AskView` 在 `done` 之后显示反馈条：认可 / 不认可（不认可弹出原因选择，选"其他"必填说明）。提交成功后置灰显示"已反馈"，不再允许改判？——允许改判，但入口收进"修改评价"小链接。
- 新增 `QaTriageView`：左侧列表（筛选 + 排序），右侧详情（成员记录、来源文档跳转、处置表单、轨迹时间线）。
- `api/qa.ts` 承载新接口，`api/retrieval.ts` 的 `AskDone` 增加 `qa_record_id`。

### D10 配置

| 配置 | 默认 | 作用 |
|------|------|------|
| `qa_triage_enabled` | `true` | 关闭后只留痕不判定，用于灰度 |
| `qa_record_retention_days` | `180` | 保留期口径 |
| `qa_low_hit_floor` | `2` | 命中数低于此值打标 |
| `qa_score_mark_threshold` | `0`（关闭） | 最高分标记线 |
| `qa_score_gap_threshold` | `0`（关闭） | 首末分差标记线 |
| `qa_cluster_similarity` | `0.92` | 归簇相似度 |

分数类阈值默认关闭：`1 - cosine_distance` 的分布随模型与语料漂移，在拿到真实分布前不设绝对值。

## Risks / Trade-offs

- **答案正文入库扩大敏感面** → 记录继承来源最高密级、随空间隔离、默认 180 天保留；治理端过滤复用 `allowed_sensitivities`，不做"遮引用露正文"的半遮挡。
- **流结束写入失败会留下 `streaming` 孤儿记录** → 孤儿不参与判定与入队，查询端只认 `completed`；后续清理作业按 `started_at` 收尾（L3 第 7 项）。
- **归簇并发产生重复空簇** → 记录级唯一约束保证不串簇；重复簇由治理者关闭为"误报"，同时成为阈值调优的样本。
- **每个问答多两次短事务** → 写入发生在流结束后，不影响首字延迟；连接池占用需在压测中确认（`db_pool_size`）。
- **`question_vec` 存储成本**（约 4KB/条）→ 依赖保留期控制总量；聚类查询限定 `status=pending`，避免全表扫描。
- **阈值默认值可能不适用** → 默认关闭分数类判定，先用"未命中 / 无引用 / 不认可"三条跑一段时间，再按真实分布开启。
- **迁移漏授 `qa:manage`** → 迁移脚本内显式 `INSERT ... SELECT id FROM role WHERE code='admin'`，并在迁移后校验 admin 角色权限数变化。

## Migration Plan

1. 新增迁移：建 `qa_record`、`qa_feedback`、`qa_issue`、`qa_issue_record`、`qa_issue_event` 及索引（含 `question_vec` / `representative_vec` 的 HNSW 与 `reasons` 的 GIN）。
2. 同一迁移内给 `role.code='admin'` 的角色补 `qa:manage`（`ON CONFLICT DO NOTHING`）。
3. 先迁移、后发版：新代码依赖新表与新权限行；旧代码在新表存在时行为不变。
4. 回滚策略：代码回滚即可，表与数据保留（无破坏性变更）；如需彻底回滚再单独 drop，且必须在保留期决策后执行。

## Open Questions

- 簇的代表向量是否随新成员更新（例如改用最近一条记录的问法）。首期固定为首条，归簇召回率不足时再调整，不影响接口与规格。
- 反馈原因取值是否需要运营可配。首期写死枚举，若后续接入多业务再改为字典表。
