# 部署与发布

容器化发布采用蓝绿两色：同一份镜像同时跑两个颜色，网关只把流量打给其中一色；
切换发布时先起新色、验证通过后再改网关指向，出问题改回来即可，不需要重建容器。

## 目录布局

```
deploy/
  gateway/
    nginx.conf            网关主配置：map 变量选上游、SSE 关缓冲、长连接超时
    conf.d/active.conf    当前生效的"颜色 + 前端目录"，由发布脚本原子重写
  releases/<version>/     前端构建产物，一个版本一个目录，只增不改
    index.html
    assets/
  state/
    current.json          当前版本、颜色、上一版本（原子写）
    releases.jsonl        发布/回滚记录，只追加
    drain.log             旧 worker 排空日志
```

- `active.conf` 用**目录挂载**给 gateway（不是单文件挂载），这样宿主上 `rename` 替换能被容器感知。
- 两个切换点（API 上游与前端目录）写在同一个文件里，一次替换同时生效，不存在"API 换了前端没换"的中间态。
- 后端镜像单份四角色：`api` / `worker` / `beat` / `migrate` 只是启动命令不同，
  保证"发布时执行的迁移"和"切流后跑的代码"是同一份产物。

## 端口

| 服务 | 监听 | 说明 |
| --- | --- | --- |
| `gateway` | `0.0.0.0:${GATEWAY_PORT:-8080}` | 唯一对外入口：`/` 前端、`/api` 反代 |
| `api-blue` | `127.0.0.1:${BLUE_API_PORT:-8000}` | 仅供发布脚本健康检查与排障，不对外 |
| `api-green` | `127.0.0.1:${GREEN_API_PORT:-8001}` | 同上 |
| 中间件 | 见 `docker-compose.yml` | PostgreSQL / Redis / Neo4j / MinIO |

两色 API 只绑宿主回环地址：外部流量必须经网关，否则绕过切流。
端口可用 `.env` 的 `GATEWAY_PORT` / `BLUE_API_PORT` / `GREEN_API_PORT` 调整；
发布与回滚脚本都从 `.env` 读取探测端口，改端口不需要改脚本。

网关除 `/` 与 `/api` 外还有两个直通端点，都不依赖前端产物：

- `/healthz`：转发到**当前颜色**的 `/healthz`，返回 `release` 与 `color`，脚本靠它确认流量真的切过去了；
- `/nginx-health`：网关自身的存活探针，上游全挂也返回 200。

## 发布模式

发布脚本支持两种模式，由 `-Mode` 指定，缺省读 `.env` 的 `RELEASE_MODE`，都未配置即 `bluegreen`：

| 模式 | 行为 | 适用 |
| --- | --- | --- |
| `bluegreen`（默认） | 起新色 → 验证 → 切流 → 排空旧色；两色并存 | 正常发布，失败零停机 |
| `inplace` | 只保留当前色，`--force-recreate` 停旧起新 | 单机资源紧张、不需要零停机 |

```powershell
pwsh -File scripts\release.ps1 -Version v20261126-1 -Mode inplace
```

单色模式的取舍：

- 重建 `api` / `worker` / `worker-mineru` / `beat` 期间服务不可用，长任务会被中断；
- 没有备用色可"保留线上"，失败时脚本按 `current.json` 的上一版本镜像 tag 原地重建回去，
  前端目录同步退回（若已被清理会给出提示）；首次发布失败则只能人工处理；
- 回滚同样不再换色，而是按目标版本镜像 tag 重建，见下文"回滚"。

## 首次部署

```powershell
copy .env.example .env          # 至少填 SEED_ADMIN_PASSWORD、EMBEDDING_API_KEY、LLM_API_KEY
docker compose up -d            # 中间件
docker compose -f docker-compose.yml -f docker-compose.release.yml up -d gateway
pwsh -File scripts\release.ps1 -Version v20261125-1
```

`release.ps1` 会自动完成建镜像、建前端版本目录、迁移、起 blue、切流；
之后浏览器访问 `http://<host>:8080`。

## 日常发布

```powershell
pwsh -File scripts\release.ps1 -Version v20261126-1
```

阶段与失败行为：

| 阶段 | 做什么 | 失败后 |
| --- | --- | --- |
| `preflight` | 校验 `.env`、compose、中间件与 gateway 在跑、版本号格式 | 直接退出，什么都不改 |
| `build` | `docker build -t kp-backend:<version>`、前端产物落 `deploy/releases/<version>/` | 同上；镜像 tag 可能残留，可手工 `docker rmi` |
| `migration-gate` | `scripts/check_migrations.py` 扫本次新增迁移 | 非零退出，不动 schema、不起新色 |
| `migrate` | 一次性容器执行 `alembic upgrade head` | 同上 |
| `start-target` | 蓝绿：起目标色的 api / worker / worker-mineru；单色：`--force-recreate` 当前色四个角色 | 蓝绿清理目标色；单色按上一版本镜像重建 |
| `readyz` | 轮询 `http://127.0.0.1:<port>/readyz`，默认 90s | 同上 |
| `smoke` | 登录取 token → `/auth/me` → `POST /retrieval/search` | 同上 |
| `switch` | 原子重写 `active.conf`，`nginx -t` 通过后 `reload`（单色模式仅校准版本目录） | 若已切流：提示执行 `rollback.ps1` |
| `drain` | 蓝绿：停旧 beat → 起新 beat → 停旧 api → 旧 worker 后台排空；单色：跳过 | 记录失败，新色继续服务 |

每次成功或失败都会往 `deploy/state/releases.jsonl` 追加一条记录（含 `stage`、`error`、`contract`、`operator`）。

演练与重试用 `-SkipBuild`（要求前端版本目录已存在）；明确本次含 contract 迁移时用 `-AllowContract`，
该标记会让这一版不可回滚。

## 回滚

```powershell
pwsh -File scripts\rollback.ps1 -Reason "检索结果异常"
pwsh -File scripts\rollback.ps1 -To v20261120-1 -Reason "指定版本"
```

- 蓝绿模式的回滚只退**流量与前端目录**，不重建镜像、不重建前端、不碰 schema：
  起回上一版 API/worker，`/readyz` 通过后改 `active.conf`，再停当前色 API 与 beat，当前色 worker 后台排空。
- 单色模式（`current.json` 的 `mode=inplace`）没有第二个颜色可切，回滚改为按目标版本镜像 tag
  `--force-recreate` 重建当前色；镜像 tag 不存在会直接报错，需要先重新构建该版本。
- 前端版本目录保留最近 3 个，回滚目标目录被清理时会直接报错，需要先重建该版本。

### 回滚边界

只要当前版本执行过 **contract 迁移**（删表删列、收紧可空、改名），`rollback.ps1` 会拒绝执行
（`result=rejected`）：数据已按新结构写过，旧代码读不懂。这种情况只能前向修复再发一版。

## 迁移纪律：expand / contract

蓝绿切换期间新旧两版代码会同时存在几分钟，因此一次发布**只允许 expand**：

| 允许（expand） | 拦截（contract） |
| --- | --- |
| 加表、加可空列、加索引 | `drop_column` / `drop_table` / `drop_constraint` |
| 加默认值、回填数据 | `alter_column(nullable=False)`（收紧可空） |
| 新增枚举值、放宽长度 | `rename_table` / 手工 `op.execute("... DROP ...")` |

分两次发布：本次 expand 并让新代码双读双写，下次再 contract 删旧结构。
`check_migrations.py` 只扫描本次改动涉及的迁移文件，可用
`python scripts/check_migrations.py --help` 查看参数；命中时打印文件与行号，
`--allow-contract` 仅在明确知道"这一版放弃回滚能力"时使用。

## 消息契约只增不改

切流窗口内新旧 worker 同时消费同一批队列，任务参数必须向后兼容：

- Celery 任务只加**带默认值**的可选参数，不改已有参数的名称与语义；
- 任务体内读取参数用 `.get(name, default)`，不要假设键一定存在；
- 消息体（payload）新增字段只加不改，删除字段至少等旧版本全部下线后再说。

违反上述约定的直接后果是旧 worker 反序列化失败、任务在队列里反复重投。

## beat 单实例

`beat` 会在每个周期投递 `watchdog.sweep` 等周期任务，两个 beat 同时跑就是双倍扫描。
因此：

- `app.workers.beat_launcher` 先用 Redis `SET NX PX` 抢锁，抢不到直接正常退出（`restart: on-failure` 不会拉起来）；
- 持锁期间用 Lua 脚本续租，锁被别人拿走时主动 `terminate` 子进程；
- 发布脚本按"先停旧 beat，再起新 beat"的顺序切换，锁只是兜底。

`BEAT_LOCK_TTL_SECONDS` 必须大于续租间隔；改小会让锁在持有者存活时过期。

## worker 排空

解析最长 `PARSE_HARD_TIME_LIMIT_SECONDS`，抽取最长到 `EXTRACT_HARD_TIME_LIMIT_SECONDS`（默认 3900s）。
切流后旧 worker 不立刻停，而是由 `_drain-worker.ps1` 在后台执行
`docker compose stop --timeout <WORKER_DRAIN_GRACE>`：

- `WORKER_DRAIN_GRACE` 默认 900s，运行长抽取任务的部署应调到大于最长任务时间（如 4200）；
- 宽限内 SIGTERM 走 warm shutdown，当前任务跑完再退出；
- 超过宽限仍未结束，容器被强制停止，任务由心跳看护流程判定为执行者消失并按重试预算重投。

排空进度写在 `deploy/state/drain.log`。

## 版本目录保留

`build-frontend.ps1` 每次构建生成一个新的 `deploy/releases/<version>/`，并按名称倒序保留最近
`-Keep`（默认 3）个，更旧的目录会被删除：

- **只增不改**：同一版本目录不会被覆盖，构建脚本发现目录已存在会直接失败；
- 保留数决定回滚纵深：要回滚更早版本，必须先用相同版本号重新构建前端；
- 磁盘占用按"版本数 × dist 体积"估算，`assets/` 是带 hash 的静态文件，可直接放宿主盘。

## 状态查询

不需要数据库，仅凭 `deploy/state` 就能回答发布状态：

```powershell
Get-Content deploy/state/current.json          # 当前版本、颜色、上一版本、切换时间
Get-Content deploy/state/releases.jsonl -Tail 3  # 最近 3 次发布/回滚，含失败阶段
```

`current.json` 通过"临时文件 + `File.Move` 覆盖"写入，模拟写一半不会留下非法 JSON；
读取时容错缺失与损坏，损坏会降级为"无状态"，此时首次发布直接走 blue。
`mode` 字段记录本次发布用的是 `bluegreen` 还是 `inplace`，回滚脚本据此选择换色还是原地重建；
没有该字段的旧状态按 `bluegreen` 处理。

## 本地 conda 开发与容器发布互斥

两者共用同一套 PostgreSQL / Redis / Neo4j / MinIO 时只能有一边在跑：

- 宿主的 `start-backend.ps1` / `start-worker.ps1` 会消费同一个 `parse` 队列，
  和容器 worker 同时运行会导致任务被两边抢，`beat` 也会与容器 beat 重复投递；
- 开发时先停容器侧的 api / worker / beat（`docker compose ... stop`，中间件可保留）；
- 容器发布期间不要在宿主起 uvicorn/celery，本地验证走 `127.0.0.1:8000`（发布脚本只做健康检查，不接外部流量）。
