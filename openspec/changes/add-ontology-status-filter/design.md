# Design

## Context

见 `proposal.md` 的 Why。当前实现要点：

- `OntologyView.vue` 的 `load()` 固定以 `include_disabled=true` 请求两类定义（`ontologyApi.classes(true)` / `ontologyApi.relations(true)`），页面内存里已经有全量数据；列表无分页、无服务端排序参数，后端按 `code` 排序返回。
- 状态目前只出现在名称单元格：`row.status === 1` 时渲染一枚「失效」标签，有效行无任何标记。
- 后端 list 接口只有 `include_disabled` 布尔开关，不接受按状态查询。
- `classMap`（父类名称解析）与 `classOptions`（关系起点/终点候选）都由 `classes` 全量数组派生。
- Element Plus 版本为 `^2.8.4`，`el-tabs` 只有 `default` 插槽，没有右侧扩展插槽。

## Goals / Non-Goals

**Goals**

- 两列表格各有一列可见的状态，并提供整页级的状态筛选与 `可见/总数` 计数。
- 筛选实现不改变全量数据的派生结果（父类名、关系候选）。
- 只改一个前端文件，后端零改动、零迁移。

**Non-Goals**

- 不做服务端按状态查询、不做分页与排序参数。
- 不改 `include_disabled` 语义，不改 API 客户端与类型定义。
- 不改其他页面（用户、审核流、解析策略）的字符串状态枚举。

## Decisions

### 1. 筛选放在前端

本体条目是「几十行」量级，且页面本来就一次性拉全量；服务端筛选会引入新查询参数、新测试，还要为关系候选另存一份全量类数据（否则筛选为「有效」时关系弹窗选不到失效类）。收益不成立，故采用客户端筛选，后端保持不变。

备选：list 接口加 `status: Literal[0,1] | None` —— 留到将来出现分页时再考虑；届时页面需要两次请求或缓存全量类。

### 2. 一个筛选状态，两个页签共用

筛选项渲染在 tabs 行右侧，视觉上属于「整页」，因此用单值 `statusFilter: 'all' | 0 | 1`，切换页签不重置。若每个页签各存一份筛选，用户切 tab 会看到筛选框值变化，与控件位置暗示的语义不符。

### 3. 用 computed 派生可见行，不改写原数组

```
classes[]  ──filter──> visibleClasses  ──> 表格 :data
    │
    ├──> classMap（父类名解析）
    └──> classOptions（关系起点/终点候选）
```

若直接把 `classes.value` 替换成过滤结果，父类列会退化成 id 片段、关系编辑下拉会丢失失效类，属于隐蔽回归。因此新增 `visibleClasses` / `visibleRelations`，原数组保持全量。

### 4. tabs 右侧布局用外层 flex

`el-tabs` 无右侧插槽，三种做法对比：

| 做法 | 结论 |
| --- | --- |
| 外层 flex：`el-tabs` 占满剩余宽度，选择框固定宽度、与 tab 头对齐 | 采用。不依赖内部 DOM 结构，升级 Element Plus 不易碎 |
| 绝对定位到 `.el-tabs__header` 右侧 | 放弃。需要预留 padding 并依赖内部类名，header 高度变化即错位 |
| 放进页签内容区的 toolbar | 放弃。与「筛选在 tab 右侧」的诉求不符 |

选择框宽度固定（约 132px），`align-items: flex-start` 使其顶部与 tab 标签行对齐。

### 5. 计数口径为「可见/总数」

页签标题用 `visibleClasses.length/classes.length`，默认「全部」时两者相等（`12/12`），筛选后能同时看到筛掉了多少。默认筛选项为「全部」，与改动前的「全量可见」行为一致。

### 6. 状态列与去重

新增「状态」列（宽约 80）：`0` → `有效` + `type="success"`，`1` → `失效` + `type="info"`。名称单元格原有的「失效」标签删除，避免同一行同一信息出现两次。

### 7. 空态按是否筛选区分

`el-empty` 的描述在筛选值非 `all` 时改为「没有符合筛选条件的实体类/关系类型」，否则保留原有「先定义实体类，抽取才有约束」「还没有关系类型」。

## Risks / Trade-offs

- [窄屏下 tabs 头与筛选框争抢宽度] → `el-tabs` 设 `min-width: 0` 允许收缩，选择框固定宽度；tab 头自身可横向滚动。
- [Element Plus 升级后内部结构变化] → 布局不依赖 `__header` 内部定位，只依赖外层 flex。
- [全量拉取在数据量增长后变慢] → 当前量级可忽略；若将来出现分页需求，再把筛选下推到接口（见决策 1 的备选）。
- [筛选状态与「已失效定义不参与抽取」语义混淆] → 页面副标题与筛选项只描述展示范围；抽取约束仍由后端 `include_disabled=false` 决定，不改动。

## Migration Plan

纯前端静态资源变更，无数据迁移。回滚即回滚该文件；页面默认「全部」时行为与改动前一致（全量展示），不产生数据副作用。

## Open Questions

无。
