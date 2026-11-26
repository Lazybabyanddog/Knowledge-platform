# Spec Delta

## Purpose

把切块方式变成解析策略的一部分：同一份解析产物可以按文档形态产出不同粒度的检索片段，且选择、生效与追溯都有确定规则。

## ADDED Requirements

### Requirement: 切块策略的配置与校验

系统 SHALL 允许在解析策略配置的 `chunk` 子对象中声明切块策略：`strategy`、`size`、`overlap`。

`strategy` SHALL 仅接受 `block_merge`、`fixed_window`、`heading_section` 三种取值；`size` SHALL 在 100 至 4000 之间；`overlap` SHALL 在 0 至 `size` 的一半之间。

系统 SHALL 拒绝未知策略名、越界数值，以及与所选策略不匹配的参数（如为 `block_merge` 指定 `overlap`），返回 400 并指出字段。

当策略配置缺少 `chunk` 子对象时，系统 SHALL 按 `block_merge` 执行，并把长度约束取为既有的 `chunk_hint`，再缺省为 800。存量策略的切分结果 SHALL 保持不变。

#### Scenario: 存量策略不受影响

- **WHEN** 一个既有策略只有 `chunk_hint=600` 而没有 `chunk` 子对象
- **THEN** 索引构建按 `block_merge` 切分，长度约束为 600
- **THEN** 产出的片段数量与该能力上线前一致

#### Scenario: 拒绝非法策略名

- **WHEN** 管理员把 `chunk.strategy` 设为 `semantic`
- **THEN** 系统返回 400，指出该策略名不被支持
- **THEN** 系统不保存策略版本

#### Scenario: 拒绝与策略不匹配的参数

- **WHEN** 管理员选择 `block_merge` 同时给出 `overlap=100`
- **THEN** 系统返回 400，指出该参数不适用于此策略

### Requirement: 切块策略的切分行为

系统 SHALL 提供三种切分策略，共同不变量为：被标记丢弃的块与空块不参与切分；表格块整体独立成片段、不与正文合并；片段内容包含其所在标题路径作为前缀；片段记录覆盖的块标识、页码范围与标题路径。

`block_merge` SHALL 合并相邻正文块直到达到长度约束，标题块作为其后片段的前缀而不独立成片段，超长单块按空行、句末、硬切三级回退切分。

`fixed_window` SHALL 以 `size` 为窗口累积内容，并按 `overlap` 让相邻片段保留重叠字符；窗口 SHALL 只在段落或句末边界结束，不得在句子中间截断，除非单句本身超过窗口。

`heading_section` SHALL 以标题块为边界成片段：同一标题下的连续正文合并为一个片段；当该节内容超过 `size` 时，SHALL 按 `fixed_window` 的规则二次切分，并保持相同的标题前缀。

#### Scenario: 固定窗口带重叠

- **WHEN** 策略为 `fixed_window`，`size=500`、`overlap=100`，输入为若干长段落
- **THEN** 相邻片段之间保留约 100 字的重复内容
- **THEN** 每个片段长度不超过 500 字（标题前缀计入长度）

#### Scenario: 按标题成节

- **WHEN** 策略为 `heading_section`，文档包含三个二级标题，每节正文各 300 字
- **THEN** 系统产出三个片段，各自的 `heading_path` 对应所在标题
- **THEN** 不同节的正文不合并到同一片段

#### Scenario: 超长小节二次切分

- **WHEN** 策略为 `heading_section`，`size=800`，某一节正文共 2000 字
- **THEN** 该节被切分为多个片段
- **THEN** 每个片段都以该节标题路径开头

#### Scenario: 表格独立成片段

- **WHEN** 任意策略下解析产物中出现表格块
- **THEN** 该表格整体构成一个片段，不与前后正文合并
- **THEN** 该片段记录表格块的标识与页码

### Requirement: 切块配置的生效与追溯

切块 SHALL 只发生在索引构建阶段，解析阶段 SHALL NOT 切分内容。

索引构建 SHALL 读取该版本最近一次成功解析任务所关联策略版本的 `chunk` 配置，并据此选择切分策略。

已建立的片段 SHALL NOT 因策略后续变更而自动改变；变更后需通过重建索引生效。

系统 SHALL 保证同一切块配置可由 `parse_task.strategy_id` 关联的策略版本追溯，片段本身无需新增策略字段。

#### Scenario: 策略变更不影响已建索引

- **WHEN** 管理员把某策略从 `block_merge` 改为 `heading_section`，但未重建索引
- **THEN** 该文档当前片段保持原样
- **THEN** 文档的索引状态仍为已就绪

#### Scenario: 重建索引采用新配置

- **WHEN** 管理员对已就绪的文档执行重建索引，此时策略已改为 `heading_section`
- **THEN** 系统按 `heading_section` 重新切分
- **THEN** 片段数量随切分结果更新

#### Scenario: 重复建索引不产生重复片段

- **WHEN** 同一解析任务被执行两次索引构建
- **THEN** 片段总数与单次构建一致
- **THEN** 不出现重复片段

### Requirement: 切块配置的展示

解析策略列表与详情 SHALL 返回 `chunk` 子对象，使管理员能看到该版本实际使用的切块策略与参数。

策略表单 SHALL 允许选择切块策略并填写长度与重叠参数，无需手工编辑 JSON。

#### Scenario: 列表展示切块配置

- **WHEN** 管理员查看策略列表
- **THEN** 每一项能看出其切块策略与长度参数

#### Scenario: 表单保存切块配置

- **WHEN** 管理员在表单中选择 `fixed_window`、`size=600`、`overlap=80` 并保存
- **THEN** 新版本策略的 `config.chunk` 为对应取值
- **THEN** 后续索引构建按该配置执行
