# Spec Delta

## Purpose

定义审批流可配置的文档类型范围，确保所有能被解析的文档类型都能通过审批进入发布状态，从而成为可被检索的知识来源。

## ADDED Requirements

### Requirement: 审批流类型覆盖全部可解析文档

审批流配置 SHALL 接受系统支持的每一种文档类型，包括 `html`，不得出现前端可选而后端拒绝的类型。

- 创建与更新审批流时，`doc_type` 取值 MUST 属于系统文档类型集合；不属于时返回参数错误。
- 文档提交审批时 MUST 按自身 `doc_type` 匹配已启用的默认审批流。
- 当对应类型尚无默认审批流时，提审 MUST 返回明确错误，提示管理员先配置该类型的默认流。
- 同一空间内每种文档类型 MUST 至多有一条默认审批流。

#### Scenario: 配置 HTML 默认审批流

- **WHEN** 管理员创建 `doc_type=html` 的审批流并设为默认
- **THEN** 保存成功，审批流列表中该类型的默认标记生效

#### Scenario: HTML 文档提审

- **WHEN** 一篇 `doc_type=html`、`status=draft` 的文档被提交审批，且空间内存在 HTML 默认流
- **THEN** 提审成功，文档状态变为 `pending`，并生成对应审批实例

#### Scenario: 缺少该类型默认流

- **WHEN** 一篇 `doc_type=html` 的文档被提交审批，但空间内没有 HTML 默认流
- **THEN** 接口返回错误，错误信息说明该文档类型尚未配置默认审批流

#### Scenario: 非法类型被拒绝

- **WHEN** 调用方以不存在的文档类型（如 `markdown`）创建审批流
- **THEN** 接口返回参数校验错误，不写入任何审批流
