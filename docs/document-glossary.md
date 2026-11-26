# 文档级别名词典：配置与行为

词典回答的是"**这篇文档里**，某个简称指谁"。它在建索引时按解析块自算，写在
`parse_task.stats["glossary"]`，问答拼上下文时按片段裁剪注入，**不进正文、不进向量、不进引用**。

跨文档的实体对齐与审核属于图谱链路，两者互不读取：图谱判定结果不会流入本文档词典，
本词典也不会反向写图谱。

## 一、配置在哪

挂在解析策略的 `config.alias`，与 `config.chunk` 并列。策略每次更新都会产生新版本，
所以"用哪套规则抽的"可以随片段追溯到 `document_chunk.parse_task_id → parse_task.strategy_id`。

```json
{
  "alias": {
    "enabled": true,
    "use_builtin": true,
    "rules": [
      {
        "name": "合同简称",
        "kind": "ALIAS",
        "triggers": ["以下简称", "简称", "下称"],
        "shapes": {
          "suffixes": ["公司", "分公司", "集团", "银行"],
          "wrappers": ["《", "》", "（", "）"],
          "stop_words": ["的规定", "的约定", "为准"],
          "max_len": 40
        },
        "target_cardinality": "ONE",
        "priority": 60,
        "regex": null
      }
    ]
  }
}
```

| 字段 | 取值 | 说明 |
| --- | --- | --- |
| `enabled` | 布尔，缺省 `true` | 关闭即不扫描；重建索引时旧的 `stats["glossary"]` 会被移除，问答立刻回到未上线前的输出 |
| `use_builtin` | 布尔，缺省 `true` | 是否叠加内置规则；内置规则不能通过自定义规则覆盖，只能整体关掉 |
| `rules` | 数组，≤20 条 | 自定义规则；`name` 不得与内置规则重名 |
| `rules[].name` | 1–64 字 | 唯一，错误信息里用它定位 |
| `rules[].kind` | `ALIAS` / `ROLE` / `RELATION` | 决定能不能产出确定项，见第二节 |
| `rules[].triggers` | 1–20 个，每个 ≤12 字 | **只定位左边界**；没有 `shapes` 的规则在保存期就被拒绝 |
| `rules[].shapes` | 必填 | `suffixes` / `wrappers` / `stop_words` / `max_len`（2–60）共同收紧右边界 |
| `rules[].target_cardinality` | `ONE` / `MANY` / `UNKNOWN` | 缺省：`RELATION` 为 `MANY`，其余为 `ONE` |
| `rules[].priority` | 0–100，缺省 50 | 同一位置被多条规则命中时，高者优先；同优先级双方都降级 |
| `rules[].regex` | 可选，≤200 字符 | 补救入口：必须能编译且含命名组 `surface`，拒绝嵌套量词 |

保存期校验失败返回 400，错误信息带规则名与字段名，且不会产生新的策略版本；
历史脏配置在读取时只让别名视图退化为 `{}`，不会导致接口 500。

## 二、`kind` 与 `target_cardinality` 是两件事

`kind` 说明"这是什么关系"，`target_cardinality` 说明"可能对应几个目标"，两者不能互相替代：

| kind | 例子 | 能否产出确定项 |
| --- | --- | --- |
| `ALIAS` | 以下简称 Y | 能：`X = Y` |
| `ROLE` | 原告、被告、申请执行人 | 能：角色在本案内前向绑定 |
| `RELATION` | 母公司、子公司、关联方 | **不能**，只产出候选 |

判定结果由三个字段组成，组合必须落在下表之内，其余组合写入前即被丢弃：

| `target_cardinality` | `certainty` | `resolution` | 合法 | prompt 里的写法 |
| --- | --- | --- | --- | --- |
| `ONE` | `CONFIRMED` | `RESOLVED` | 是 | `X = Y` |
| `ONE` | `CANDIDATE` | `PENDING` | 是 | `X 可能指 Y` |
| `MANY` | `CANDIDATE` | `PENDING` | 是 | `X 可能指 A / B / C` |
| `MANY` | `CONFIRMED` | — | 否 | 不可能"已消解"到唯一 |
| `ONE` | `CONFIRMED` | `PENDING` | 否 | 确认了就不该待处理 |
| `UNKNOWN` | 任意 | 任意 | 否 | 一律按 `MANY` 保守处理 |

因此"子公司"这类一对多称谓永远只是候选：同一表面形式命中多个规范名、规则冲突、
缺证据三种情况都降级为候选，降不下去就丢弃。系统**不做**"首现优先""最近实体优先"——
那等于把不确定伪装成确定。

## 三、什么时候算

1. **建索引时**：`build_version_index` 在切块之后扫描该版本的解析块，判定并写快照。
   纯计算、无模型调用；任何异常只写 `status=degraded` 与原因，索引照常 `READY`。
2. **问答时**：检索返回后按 `parse_task_id` 一次批量装载词典，再按片段裁剪——
   只注入片段里**实际出现**的别名（优先 `block_ids` 交集，兜底文本包含）。

注入形态（编号、标题、正文一字不改）：

```
[1] 《民事起诉状》第 1 页
本文简称：鲁潍公司 = 山东鲁潍机械有限公司。
原告山东鲁潍机械有限公司（以下简称鲁潍公司）诉称……
```

单片段最多 5 条，确定项优先、再按 `priority` 截断；候选措辞用"可能指"，
文本里最多列 3 个候选。没有条目时 `render_glossary` 返回空串，输出与未启用时逐字一致。

## 四、怎么查

- `GET /api/v1/parse-tasks/{task_id}`：`glossary` 字段返回 `status`、`counts`（raw / confirmed /
  candidate / dropped）、命中规则与条目（最多 50 条，超出置 `truncated=true`）。
- `POST /api/v1/retrieval/ask`：`done` 事件带 `glossary_lines`（本次注入条数），零命中为 0。
- `GET /api/v1/parse-strategies/{id}`：`alias` 字段返回补齐缺省值后的生效配置；
  `config` 字段仍是库中原始配置，两者不混用。

## 五、开关与回滚

- `enabled=false`：不扫描、不写入；已存在的词典在重建索引时被移除。
- 删除某条自定义规则只会改变后续重建的结果，不会追溯改写已有快照。
- 回滚无需改表：关掉开关或直接忽略 `stats.glossary` 即可，读取路径不会因它缺失而报错。

## 六、上限

| 项 | 上限 | 超限行为 |
| --- | --- | --- |
| 自定义规则 | 20 条 | 保存期 400 |
| 快照条目 | 200 条 | 截断并记 `truncated=true`，`counts` 与实际条数一致 |
| 快照字节 | 64KB | 逐条截断直到满足 |
| 详情返回条目 | 50 条 | 截断并置 `truncated=true` |
| 单片段注入行 | 5 条 | 按确定项与优先级截断 |
