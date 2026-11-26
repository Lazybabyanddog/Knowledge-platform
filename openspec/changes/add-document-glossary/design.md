# Design

## Context

见 `proposal.md - Why`。约束来自现状：

- 索引构建一次拿到该版本的全部解析块，且已经加载 `ParseTask`、`ParseStrategy`、`Document`（`app/services/index_service.py::build_version_index`），因此"文档级"扫描在这一处做最便宜，不需要新查询、新表。
- `parse_task.stats` 是现成的 JSONB 列，索引构建时 `task` 已在会话中。
- 问答上下文由 `app/services/retrieval_service.py::build_ask_messages(question, hits)` 单点拼装，`ChunkHit` 已携带 `block_ids`、`title`、页码。
- 配置范式已有先例：`config.chunk` = 解析函数 + 保存期校验 + 读取视图 + 缺省回退（`app/services/chunk_config.py`、`app/schemas/parse.py`）。
- 实体抽取是独立任务（`extraction_task`，分批 + 断点），与索引构建不同步；图谱侧已有 `proposed/confirmed` 状态机。

## Goals / Non-Goals

**Goals:**

- 别名对照的产生点前移到索引构建，文档内自足，不依赖图谱、不依赖抽取任务。
- 判定结果的结构能表达"确定 / 候选 / 弃置"，且非法组合写不进快照。
- 注入是纯读时行为：不碰片段正文、不碰向量、关掉即恢复原状。

**Non-Goals:**

- 不做跨文档别名索引与查询扩展（需要 workspace 级可查找结构，见 Open Questions）。
- 不做实体消歧、实体对齐、审核队列——那是图谱链路。
- 不引入 LLM 调用。本期只有规则，没有模型。

## Decisions

### D1 词典落在 `parse_task.stats["glossary"]`

写在该次索引所依据的解析任务上，键为 `glossary`。

- 归属天然正确：`document_chunk.parse_task_id` 已存在，片段能定位到自己的词典归属。
- 零迁移、零重建：不动 `document_chunk`、不动 embedding，历史只需按需重建索引。
- 生命周期与解析产物一致：解析任务换了，词典随之换。

替代方案与不采用的原因：

| 方案 | 不采用的原因 |
|---|---|
| `document_chunk` 新增 `metadata` 列 | 需要迁移；别名是文档级属性，复制到每个片段会放大存储与不一致风险 |
| 写进 `document_chunk.content` | 改变向量语义、污染引用原文、改规则要重建索引 |
| 独立 `document_glossary` 表 | 需要建表 + 回填 + 清理策略；词典是派生物，可以随时重算，不值得持久主键 |
| 复用图谱实体的 `aliases` | 跨文档并集，回答不了"这篇文档里指谁"；且抽取任务分批、异步、可能缺失 |

注意实现细节：JSONB 列必须整体重新赋值一个新 `dict`，原地 `stats[key] = ...` 不会被 SQLAlchemy 识别为变更。

### D2 自算，共享判定，不共享数据

索引构建内直接扫描 `blocks`，产出条目；判定逻辑写成纯函数，输入是"命中 + 上下文"，输出是带判定字段的条目。

```
blocks(全文) --规则执行器--> raw hits --判定器--> entries --快照--> parse_task.stats
                                                    |
                                          (图谱侧未来可复用同一判定函数,
                                           但写入自己的存储, 互不读取)
```

因此图谱侧后续若也要"确定/候选"的判定口径，可以直接复用判定器，但不共享数据、不共享生命周期。

### D3 配置挂在 `config.alias`，沿用 chunk 的四件套

```
parse_alias_config(config)   -> AliasConfig      (抛 AliasConfigError)
_check_alias_config(value)   -> dict             (请求层 400)
alias_config_view(config)    -> dict             (脏数据返回 {})
缺省                         -> enabled=true, use_builtin=true, rules=[]
```

替代方案：独立 `semantic_rule` 表 + 策略只存引用 + 索引时快照。它换来"跨策略复用规则"和"改规则不必给策略升版本"，但需要建表、CRUD、引用完整性校验，而且一期还没出现"三条策略共用一套规则"的真实需求。触发条件写清楚：当同一工作空间出现三份以上重复规则、或规则改动需要批量生效时，再抽表；届时 `config.alias.rules` 是现成的迁移来源。

`enabled`/`use_builtin` 缺省为真：能力是纯增量，且"抽不到就不注入"使未命中路径与关闭路径完全等价，因此默认开启不破坏既有行为。

### D4 一条规则 = 触发 × 形态 × 类型 × 基数

```json
{
  "name": "通用简称",
  "kind": "ALIAS",                 // ALIAS | ROLE | RELATION
  "triggers": ["以下简称", "简称", "下称"],
  "shapes": {
    "suffixes": ["公司", "分公司", "局", "委员会", "中心"],
    "wrappers": ["《", "》", "“", "”", "（", "）"],
    "stop_words": ["的规定", "的约定", "为准", "应当"],
    "max_len": 40
  },
  "target_cardinality": "ONE",     // ONE | MANY | UNKNOWN
  "priority": 50,
  "regex": null                    // 可选补救入口
}
```

关键点：**只有 `triggers` 没有 `shapes` 的规则不可用**——触发词只能定位左边界，右边界必须由"后缀词 / 成对标记 / 停顿词 / 长度上限"共同收紧，否则抽出来的"简称"会一路吃到句号。

`kind` 的语义差别决定了能不能产出断言：

| kind | 例子 | 典型基数 | 能否产出确定性对照 |
|---|---|---|---|
| `ALIAS` | 以下简称 Y | ONE | 能 |
| `ROLE` | 原告、被告、申请执行人 | ONE（前向绑定本案当事人） | 能，标注为角色绑定 |
| `RELATION` | 母公司、子公司、关联方 | MANY / UNKNOWN | 不能，只能候选 |

### D5 判定用状态矩阵，不用一条启发式链

```
certainty      = CONFIRMED | CANDIDATE
resolution     = RESOLVED  | PENDING
cardinality    = ONE | MANY | UNKNOWN
```

合法组合（校验函数直接拒绝其余组合）：

| cardinality | certainty | resolution | 合法 | 生成侧表现 |
|---|---|---|---|---|
| ONE | CONFIRMED | RESOLVED | 是 | "A（本文简称）= B" + 证据块 |
| ONE | CANDIDATE | PENDING | 是 | "A 可能指 B" |
| MANY | CANDIDATE | PENDING | 是 | "A 可能指 B/C/D" |
| MANY | CONFIRMED | — | 否 | 不可能"已消解"到唯一 |
| ONE | CONFIRMED | PENDING | 否 | 确认了就不该待处理 |
| UNKNOWN | 任意 | 任意 | 降级 | 一律按 MANY 处理 |

`resolution_policy` 本期只有 `evidence_first_then_drop` 一个取值：有唯一证据就确定，否则降级，降不下去就丢。**不实现首现优先 / 最近实体优先**，也不预留它们的开关——预留就等于允许被打开。

### D6 冲突的键是 (surface, canonical)

- 同一 `surface` 映射到多个 `canonical`：不是冲突，是基数问题 → 全部 `CANDIDATE`。
- 同一 `(surface, canonical)` 被判成不同 `certainty`：才是冲突 → 取低者（`CANDIDATE`）。
- 两条规则命中同一片段位置：按 `priority` 高者优先，同优先级则双方都降级为 `CANDIDATE`。

### D7 注入在 `build_ask_messages` 内，按片段裁剪

`ChunkHit` 增加只读字段 `glossary: tuple[GlossaryLine, ...]`，由检索后的一次批量装载填充（按 `parse_task_id` 分组，一次查询取回，避免每条片段一次查询）。

注入形态（不改变 `[n]` 编号与正文）：

```
[1] 《…》第 3 页
本文简称：苏州盐务局 = 江苏省苏州市盐务管理局；"子公司"可能指 A / B。
<片段原文>
```

- "实际出现"判定：优先 `entry.block_id ∈ hit.block_ids`（精确），兜底 `surface in hit.content`（规则宽一点）。
- 单片段上限 5 条，按 `CONFIRMED` 优先、再按 `priority` 截断。
- 对照行 MUST 与正文分行，便于按行剥离；`hit_to_source` 的 `snippet` 只取 `hit.content`，天然不含对照。

### D8 规则执行器是纯函数，失败降级为空

```
scan(blocks, ruleset) -> list[RawHit]        # 只做定位与边界裁剪
judge(raw_hits, blocks) -> list[Entry]       # 只做判定与组合校验
serialize(entries, ruleset) -> dict          # 只做快照裁剪与计数
```

`scan` 内对每条匹配设"最大步数 / 最大条目数"，命中上限即停止；整体异常被 `build_version_index` 捕获，写 `stats["glossary"] = {"status": "degraded", "error": ...}`，索引继续走向 `READY`。

### D9 快照带规则版本，便于事后解释

```json
{
  "status": "ok",
  "builtin_version": 1,
  "rules": [{"name": "通用简称", "kind": "ALIAS", "priority": 50, "source": "builtin"}],
  "counts": {"raw": 12, "confirmed": 5, "candidate": 4, "dropped": 3},
  "entries": [
    {"surface": "苏州盐务局", "canonical": "江苏省苏州市盐务管理局",
     "kind": "ALIAS", "certainty": "CONFIRMED", "resolution": "RESOLVED",
     "candidates": [], "block_id": "…", "rule": "通用简称"}
  ]
}
```

上限：条目 200 条、序列化后 64KB，超出按 `priority` 截断并记 `truncated=true`。词典是派生物，截断不报错。

## Risks / Trade-offs

- **错误对照进入 prompt** → 确定性/候选分流 + 一对多默认弃 + 单片段上限 + 关闭开关即回滚；规则可按策略灰度。
- **自由正则引发 ReDoS / 空匹配死循环** → 保存期编译 + 拒绝嵌套量词 + 长度上限 + 扫描时步数上限 + `finditer` 空匹配显式跳过。
- **右边界收不紧，抽出整句** → `shapes` 强制参与裁剪；`max_len` 硬上限；无 `shapes` 的规则在校验期就被拒绝。
- **`parse_task.stats` 膨胀** → 条目与字节双上限；只存注入所需字段，不存原文片段。
- **索引构建变慢** → 扫描是单次线性遍历 + 每条规则一次 `finditer`，无 IO、无模型；若块数极大，靠条目上限提前终止。
- **与 `add-chunking-strategies-and-mineru` 冲突** → 两者都改 `build_version_index` 与 `parse_task.stats` 的使用方式；本变更只在 `build_chunks` 之后追加一步，不修改切块参数与片段字段，合并时按"先切块、后扫描"的顺序接入即可。
- **默认开启带来的隐性行为变化** → 用"未命中即不注入"保证等价性，并在规格中断言关闭时消息逐字一致。

## Migration Plan

1. 不需要 schema 变更，也不需要数据回填。
2. 上线后，新构建索引的文档自动带词典；存量文档按需重建索引即可获得，不重建也不报错（词典为空）。
3. 灰度：逐策略打开/关闭 `config.alias`；`enabled=false` 立即回到原行为，无需重建。
4. 回滚：关开关即可；`stats.glossary` 是无引用负担的派生物，保留不影响任何读取路径。

## Open Questions

- 内置规则集的初始词表范围（机构后缀、停顿词）先给一组窄词表，按漏抽样本迭代；不影响规格与任务拆分。
- 跨文档查询扩展何时做：需要一个可查找的 workspace 级别名结构（`stats` 上的 GIN 索引或独立表），等实测出现"全称问句召回不到简称片段"的样本后再定。
