# Spec Delta

## Purpose

让 PDF 能借助本机部署的 MinerU 获得版面级结构化产物，并让该引擎的触发条件、失败分类、队列占用对运维与用户都是可预期的。

## ADDED Requirements

### Requirement: MinerU 引擎的注册与策略校验

系统 SHALL 提供名为 `mineru` 的解析引擎，且该引擎只声明支持 `pdf` 文档类型。

系统 SHALL 在保存解析策略时校验引擎存在，且引擎支持的文档类型包含该策略的 `doc_type`；不满足时返回 400，并且不保存策略版本。

系统 SHALL 在策略指定 `engine=mineru` 时使用 MinerU 引擎解析 PDF，不得静默替换为其它引擎。

#### Scenario: 保存使用 MinerU 的 PDF 策略

- **WHEN** 管理员为 `doc_type=pdf` 创建策略且 `engine=mineru`，MinerU 服务地址已配置
- **THEN** 系统保存策略并返回其标识
- **THEN** 该策略触发的解析任务使用 `mineru` 引擎

#### Scenario: 引擎不支持该文档类型

- **WHEN** 管理员为 `doc_type=office` 创建策略且 `engine=mineru`
- **THEN** 系统返回 400，说明该引擎不支持此文档类型
- **THEN** 系统不保存策略

#### Scenario: 未注册的引擎

- **WHEN** 策略的 `engine` 取值不在引擎注册表中
- **THEN** 系统返回 400 并指出未注册的引擎名

### Requirement: MinerU 产物到内容块的映射

系统 SHALL 把 MinerU 返回的结构化结果映射为有序内容块，映射规则为：标题→标题块（携带层级）、正文→段落块、表格→表格块（内容为 markdown 文本）、列表→列表块、图片→图片块。

系统 SHALL 为每个内容块保留页码；MinerU 提供了坐标时 SHALL 一并保留。

公式块 SHALL 以段落块承载，内容保留 LaTeX 文本，并在块 `metadata` 中以 `kind=formula` 标记。

图片块 SHALL NOT 下载或转存图片资源，只在 `metadata` 中登记来源标识；解析结果 SHALL 保持可用，不因图片不可取到而失败。

当 MinerU 对部分页返回失败或降级结果时，系统 SHALL 保留其余页的产物，并在解析告警中记录受影响页。

#### Scenario: 标题与段落还原

- **WHEN** MinerU 返回包含二级标题与三段正文的第 3 页结果
- **THEN** 系统产出层级为 2 的标题块与三个段落块，页码均为 3
- **THEN** 块的序号按原文顺序排列

#### Scenario: 表格与公式

- **WHEN** 页面包含一张表格与一个独立公式
- **THEN** 系统产出表格块，内容为 markdown 表格文本
- **THEN** 系统产出段落块承载公式的 LaTeX 内容，且 `metadata.kind` 为 `formula`

#### Scenario: 部分页失败

- **WHEN** MinerU 对其中的第 7 页返回失败，其余页正常
- **THEN** 系统保留其余页内容块
- **THEN** 解析告警中包含第 7 页的失败说明

### Requirement: MinerU 的超时与错误分类

系统 SHALL 为 MinerU 调用设置独立超时，该超时来自配置项而非硬编码。

系统 SHALL 把连接失败、调用超时与服务端 5xx 归为可重试错误码 `mineru_unavailable`，使任务重试预算生效。

系统 SHALL 把响应结构不合法、缺少可解析正文、输入不被接受（如加密或超限文件）归为不可重试错误码 `parse_error`。

系统 SHALL NOT 在 MinerU 失败时自动改用 `pdf_layout` 重试，失败原因 SHALL 直接暴露在任务详情中。

#### Scenario: 服务未启动

- **WHEN** MinerU 服务未监听端口，解析任务调用失败
- **THEN** 任务失败且 `error_code=mineru_unavailable`
- **THEN** 该错误被标记为可重试，自动重试预算按既有规则消耗

#### Scenario: 产物不可解析

- **WHEN** MinerU 返回 200 但响应中没有可用的内容列表
- **THEN** 任务失败且 `error_code=parse_error`
- **THEN** 系统不因该错误自动重试

#### Scenario: 超时

- **WHEN** MinerU 调用超过配置的超时时间
- **THEN** 调用被中止，任务失败且 `error_code=mineru_unavailable`

### Requirement: MinerU 解析的队列隔离

系统 SHALL 把 `engine=mineru` 的解析任务投递到独立队列 `parse.mineru`，其它解析任务、抽取任务、索引任务与看护任务 SHALL 继续使用原队列。

系统 SHALL 在人工重试与看护重投时沿用同一路由规则，使 MinerU 任务始终回到 `parse.mineru`。

当 `parse.mineru` 无消费者时，任务 SHALL 保持待执行状态，不得被丢弃或改投其它队列。

#### Scenario: 长任务不阻塞其它环节

- **WHEN** 一个 MinerU 解析任务运行 12 分钟
- **THEN** 同一时间投递的索引构建任务仍在原队列被执行
- **THEN** 看护任务按周期正常扫描

#### Scenario: 重投仍走专用队列

- **WHEN** 看护发现一个 MinerU 解析任务心跳过期并重新投递
- **THEN** 该任务进入 `parse.mineru` 队列

### Requirement: MinerU 连接配置与可用性校验

系统 SHALL 从环境配置读取 MinerU 服务地址、调用超时、语言与后端模式，服务地址 SHALL 默认为空。

系统 SHALL 在服务地址为空时拒绝创建或修改 `engine=mineru` 的策略，返回 400 并提示先配置服务地址。

系统 SHALL 支持在策略配置中覆盖表格与公式识别开关，缺省时使用服务端默认。

#### Scenario: 未配置服务地址

- **WHEN** 管理员在未设置 MinerU 服务地址的环境创建 `engine=mineru` 策略
- **THEN** 系统返回 400，提示先配置服务地址
- **THEN** 系统不保存策略

#### Scenario: 策略级开关生效

- **WHEN** 策略配置关闭公式识别
- **THEN** 发往 MinerU 的请求不启用公式识别
- **THEN** 解析产物中不出现 `kind=formula` 的段落块

### Requirement: 引擎候选查询

系统 SHALL 提供接口返回已注册引擎的名称、展示名与支持的文档类型。

前端 SHALL 以该接口返回的结果作为策略表单的引擎候选，不得内置与注册表不一致的引擎列表。

#### Scenario: 查询引擎候选

- **WHEN** 管理员打开解析策略编辑表单
- **THEN** 引擎候选为注册表中的实际项（当前为 `pdf_layout`、`office`、`ocr`、`plain_text`、`html_dom`、`mineru`）
- **THEN** 选中引擎后，文档类型候选按其支持的文档类型过滤
