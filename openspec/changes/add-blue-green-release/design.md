# Design

## Context

动机见 `proposal.md - Why`；对外行为要求见 `specs/blue-green-release/spec.md`。这里只记录决定实现形态的现状与约束。

现状（已核对）：

| 维度 | 事实 | 出处 |
| --- | --- | --- |
| 运行形态 | API 与 worker 跑宿主 conda；`uvicorn` 前台 + `--reload`；无容器镜像、无反向代理、无 CI 配置 | `scripts/start-backend.ps1:83`、`docker-compose.yml:3` |
| 中间件 | compose 只含 Postgres(pgvector) / Neo4j / Redis / Silo(S3)，命名卷持久化 | `docker-compose.yml` |
| Python | 3.11，纯 pip 依赖，无系统级二进制；`pypdf` 被 `pdf_layout.py` 导入但**未在 `pyproject.toml` 声明** | `backend/pyproject.toml`、`engines/pdf_layout.py:12` |
| 前端 | Vite 5 + Vue 3，构建产物 `dist/index.html` + `assets/*-[hash].js`，API 基址为相对路径 `/api/v1` | `frontend/package.json`、`src/api/http.ts:53` |
| 探针 | 仅有 `/healthz`，固定返回 `{status, env, version}`，不检查依赖 | `app/main.py:45-47` |
| 流式 | `POST /retrieval/ask` 返回 `text/event-stream`，已按 `request.is_disconnected()` 中止上游 | `api/v1/retrieval.py:141,181` |
| 后台任务 | 单队列 `parse`，`--pool=solo`，beat 周期触发 `watchdog.sweep`；无 beat 单实例保护 | `celery_app.py:43-60`、`scripts/start-worker.ps1:38` |
| 迁移 | Alembic 15 个版本，均实现 downgrade，但无 expand/contract 约定 | `backend/alembic/versions/` |

关键约束：

- 只有**一台**宿主机，没有集群、没有外部负载均衡；蓝绿只能在单机上用两个端口 + 一个本地网关实现。
- 开发宿主是 Windows，发布脚本必须能在 PowerShell 7 下执行；容器内部是 Linux。
- 配置全部来自 `.env`（含 JWT 密钥、LLM/Embedding 密钥），不得烘进镜像。
- 长任务硬上限为解析 960s、抽取 3900s（`.env.example:86-92`），"等任务跑完再退出"不可能无条件成立。

## Goals / Non-Goals

**Goals:**

- 单机 Docker 环境下的零停机切换：切换过程不产生 5xx、不断开已建立的 SSE 流。
- 不重新构建即可回滚到上一版本（前端静态资源与 API 同步回退）。
- 两版共存期间，数据库结构、Celery 消息、周期性任务三处不互相破坏。
- 发布/回滚是**一条命令**，并把结果写进可查询的发布记录。
- 保留现有 conda 启动方式作为本地开发路径，不被破坏。

**Non-Goals:**

- 不做多机、跨可用区高可用；网关与宿主机仍是单点。
- 不引入 Kubernetes、Service Mesh、Argo Rollouts。
- 不做数据库自身的蓝绿（双库双写）或按请求灰度的金丝雀。
- 不建设完整 CI/CD 平台；本次只提供可在本机/单机执行的发布脚本。
- 不改动业务 API 契约与既有能力的对外行为。

## Decisions

### D1 编排载体：compose 叠加文件 + 颜色后缀服务

`docker-compose.yml` 保持"只有中间件"不变，新增 `docker-compose.release.yml` 定义发布层：

```
                    +--------------------------------------+
   :8080  --------> | gateway (nginx)                      |
                    |  active.conf: upstream + 前端 root   |
                    +----+------------------------+--------+
                         |                        |
              +----------v---------+   +----------v---------+
              | api-blue  :8000    |   | api-green :8001    |
              | worker-blue        |   | worker-green       |
              | beat-blue(可选)    |   | beat-green(可选)   |
              +----------+---------+   +----------+---------+
                         |                        |
                         +-----------+------------+
                                     |
             Postgres / Redis / Neo4j / Silo（沿用现有 compose，同一网络）
```

- 两色服务名不同（`api-blue` / `api-green`），用 compose `profiles: [blue]` / `[green]` 控制哪一色在跑；两色可同时 up。
- 两色共享 `.env`，只通过 `RELEASE_COLOR` / `RELEASE_VERSION` 区分，避免两份配置漂移。
- 只把 `8000/8001` 绑到 `127.0.0.1`（供脚本探针与本机调试），外部只认 `8080`。

备选：K8s Deployment + Service selector（否决：单机场景成本过高）；systemd 双实例（否决：无法解决镜像产物一致性与前端版本目录）。

### D2 网关：nginx + 单一 `active.conf`，切换 = 重写 + reload

`deploy/gateway/active.conf` 同时承载两个切换点：

```
upstream app_backend { server api-green:8000; }     # API 指向
# 前端版本目录
# root /srv/www/v20261125-1;
```

切换脚本原子写临时文件 → `mv` 覆盖 → `nginx -s reload`。`reload` 是平滑的：旧 worker 继续处理已建立连接，新连接进入新配置。

为什么不用 Caddy admin API（虽然可以 PATCH upstream 而不 reload）：引入第二个 HTTP 服务器与一套新的配置模型，换来的只是省一次 reload；而 nginx reload 在这个项目规模下没有可观测的抖动。

为什么不用符号链接切前端：Windows 宿主创建 symlink 需要开发者模式/管理员权限，脚本会在 CI 或普通账户下失败。改为让 `active.conf` 的 `root` 直接指向版本目录，与 upstream 在同一次重写中生效——两个切换点变成一次原子操作。

### D3 前端静态资源：版本目录 + 分层缓存

```
deploy/releases/
  v20261124-1/{index.html, assets/*}
  v20261125-1/{index.html, assets/*}     <- 当前
```

- 构建输出直接落到新版本目录，**不覆盖**任何既有目录；保留最近 N 个（默认 3）供回滚与"切换前已打开的页面懒加载旧 chunk"。
- 缓存策略：`index.html` 用 `Cache-Control: no-cache`，`assets/*` 用 `immutable, max-age=31536000`（文件名含 hash）。这两条同时解释并满足 spec 中"已打开页面切换后仍能加载资源"。

### D4 后端镜像：单镜像四角色

一个 `backend/Dockerfile`（`python:3.11-slim`，非 root 用户，`pip install --no-cache-dir .`），启动命令由 compose 覆盖：

| 角色 | 命令 |
| --- | --- |
| api | `uvicorn app.main:app --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 30` |
| worker | `celery -A app.workers.celery_app:celery_app worker -Q parse -l info --pool=solo` |
| beat | `celery -A app.workers.celery_app:celery_app beat -l info` |
| migrate | `alembic upgrade head`（一次性容器，退出即结束） |

同一镜像保证"发布时跑的迁移代码"与"切流后跑的运行时代码"完全一致。镜像内加启动自检：导入全部解析引擎（`from app.parsing import engine_names`），把 `pypdf` 这类"环境里有、声明里没有"的依赖在启动时暴露，而不是等用户上传 PDF 才 500。

> 需要同步修正：`pypdf` 加入 `backend/pyproject.toml` 的 `dependencies`。这是本次容器化会立刻暴露的缺口。

### D5 探针：liveness 与 readiness 分离

| 端点 | 语义 | 内容 | 失败后果 |
| --- | --- | --- | --- |
| `/healthz` | 进程存活 | 固定轻量返回 + `version` / `color` | 容器被重启 |
| `/readyz` | 能否接流量 | Postgres `SELECT 1`、Redis `PING`、S3 `bucket_exists`、Neo4j `RETURN 1` | 不切流 |

- 必需依赖：Postgres、Redis、S3。任一失败 → `503`。
- 可降级依赖：Neo4j。失败 → `200` + `degraded: ["neo4j"]`，因为检索与文档主链路不依赖它，图谱接口按既有契约报错。
- 每项检查 1.5s 超时、整体 3s 缓存，避免探针本身把依赖打满（脚本每 2s 轮询一次）。
- 为什么不复用 `/healthz`：liveness 一旦检查 DB，DB 抖动会触发容器反复重启，反而放大故障。

### D6 排空：API 走 graceful shutdown，worker 走共存窗口，beat 走锁

**API（切流后旧色）**

1. `nginx reload` 后旧色不再有新连接；
2. 发 `SIGTERM`，uvicorn 停止 accept，等待在途请求；
3. SSE 已在 `_event_stream` 内检查 `is_disconnected()`，客户端断开即终止上游；
4. `--timeout-graceful-shutdown 30` 与 compose `stop_grace_period: 45s`，超出后强制。

**worker（不跟随切流同步下线）**

worker 不承载用户连接，且任务可能跑几十分钟。直接随切流停机会把在跑任务打成"执行者消失"，触发看护重投——虽然 `task-retry-and-recovery` 能兜住，但会浪费已完成的工作。因此：

- 切换只停旧 **API**；旧 worker 继续消费，作为共存窗口的一部分；
- 旧 worker 下线由 `release.ps1` 的收尾步骤显式触发（`celery control shutdown`，warm），宽限 `WORKER_DRAIN_GRACE`（默认 900s，可配）；
- 超过宽限仍未结束的任务允许被强制终止，由既有看护 + 断点机制接管；这是刻意接受的代价，而不是静默丢任务。

**beat（必须唯一）**

- 两色都定义 beat 服务，但启动时先抢 Redis 锁（`SET NX PX` + 周期续租），抢不到直接退出并打日志；
- 切换脚本按"先停旧 beat → 再起新 beat"顺序执行，锁只是兜底，防止手工 `compose up` 造成双触发。

备选：按版本分队列（`parse.v1` / `parse.v2`）彻底隔离新旧 worker（否决：需要改路由与监控，且队列积压会翻倍；当前以"消息契约兼容"换取简单）。

### D7 迁移：expand / contract 两阶段，发布只跑 expand

| 阶段 | 何时执行 | 允许的变更 |
| --- | --- | --- |
| expand | 本次发布，**切流前** | 建表、加可空列、加索引、放宽约束、回填数据 |
| contract | 下一次发布（旧版本已下线） | 删列、改非空、重命名、删旧索引 |

- 版本文件命名 `<rev>_expand_<desc>.py` / `<rev>_contract_<desc>.py`，让 `alembic history` 一眼可见阶段。
- `scripts/check_migrations.py` 扫描本次新增迁移中的 `op.drop_*`、`op.alter_column(nullable=False)`、`op.rename_table`，命中则要求显式 `-AllowContract` 才继续；这是提示性门禁，不做 AST 级完备性保证。
- 迁移失败 → 停止发布、不切流；因为只含 expand，旧版本在"半迁移"的库上仍可工作。
- **不回滚 schema**：`downgrade` 在共享库上会连带破坏仍在服务的旧版本。回滚只回退流量与前端目录。

### D8 消息契约：只增不改

- 任务名（`parse.document` 等）与队列名保持不变；
- 参数新增字段一律可选 + 默认值，禁止改既有字段含义；
- 幂等键沿用现有 `task_id + attempt` 语义，跨版本一致；
- 共存窗口 = 旧 worker 排空期，通常为一次发布到下一次发布之间。

### D9 发布记录：宿主文件，不写数据库

`deploy/state/releases.jsonl`（只追加）+ `deploy/state/current.json`（原子 rename）。

选文件而非数据库表的原因：发布工具不能依赖被发布系统自身的可用性——数据库迁移失败时恰恰最需要写记录。字段：`version`、`color`、`migrations`、`readyz`、`smoke`、`switched_at`、`operator`、`result`、`error`、`rollback_of`。

### D10 脚本流程

`scripts/release.ps1 -Version <v> [-SkipBuild] [-AllowContract]`：

```
预检(docker/.env/版本格式) -> 构建镜像+前端 -> 目标色=非active
   -> 迁移(expand) -> 起目标色 api/worker
   -> 轮询 /readyz(90s) -> 只读冒烟(登录 + retrieval/search)
   -> 重写 active.conf(upstream+root) + reload
   -> 停旧 beat / 起新 beat -> 旧 API 排空 -> 旧 worker 延迟下线
   -> 写发布记录
任一步失败: 停目标色, active 不变, 记录失败阶段与错误摘要
```

`scripts/rollback.ps1 [-To <version>]`：读 `current.json.previous` → 校验目标色 `/readyz` → 重写 `active.conf` → reload → 记录；若最近一次发布包含 contract 迁移则拒绝并给出冲突版本。

## Risks / Trade-offs

| 风险 | 缓解 |
| --- | --- |
| gateway 或宿主机本身故障导致全站不可用（单点未消除） | `restart: unless-stopped` + 启动自检；明确列为非目标，不假装解决 |
| 两色同时运行导致内存翻倍（API×2 + worker×2） | worker 旧色按共存窗口尽快下线；容器加 memory limit，超限先告警 |
| 长任务超过 worker 排空宽限被强杀 | 依赖既有看护重投 + 断点续跑；宽限可调，默认 900s |
| `pypdf` 等隐式依赖在镜像里缺失 | 镜像启动自检导入全部引擎；同时补齐 `pyproject.toml` |
| uvicorn 旧版本不支持 `--timeout-graceful-shutdown` | 升级并固定 `uvicorn[standard]>=0.29` |
| 迁移静态扫描漏掉破坏性变更 | 保留 `alembic check` + 发布前人工 review 清单；脚本门禁只做兜底 |
| 用户浏览器强缓存旧 `index.html`，切换后指向已删除资源 | `index.html` 设 `no-cache`；版本目录保留 3 个不删 |
| 手工在错误颜色上起 beat 造成双跑 | Redis 单实例锁 + 启动即退出 |
| 宿主 Windows 下脚本与 Linux 路径差异 | 脚本只操作 compose 与宿主文件；容器内路径由 compose 挂载固定 |

## Migration Plan

1. **容器化落地（不改流量）**：补 Dockerfile / release compose / gateway，先用单色（blue）跑通全量功能与解析链路；此时外部仍可继续用原 conda 方式，二者对同一中间件互斥使用。
2. **探针与排空**：上线 `/readyz`、`--timeout-graceful-shutdown`、worker warm shutdown、beat 锁；用"同版本重发"演练一次完整发布流程（不产生版本变化，只验证流程与排空）。
3. **首次真实蓝绿**：`v1` 跑 blue → `v2` 起 green → 切流 → 观察 → 排空 blue。
4. **回滚演练**：在业务低峰执行一次"切到 green 后立即 rollback 回 blue"，确认时间与前端目录同步。
5. **退路**：容器化上线失败时，恢复 `scripts/start-backend.ps1` + conda 环境即可回到发布前形态；中间件与数据卷未被修改。

## Open Questions

- 目标宿主是 Linux 还是 Windows？（影响脚本可移植性、文件权限与 `--pool=solo` 是否需要改回 prefork）
- TLS 在哪一层终止：gateway 直接持证书，还是外层已有反向代理/域名接入？
- 现有命名卷是否需要改为宿主固定路径（bind mount），以便备份与迁移？
- 冒烟用的账号：是否新建一个只读的 `release-smoker` 账号，避免污染管理员审计日志？
