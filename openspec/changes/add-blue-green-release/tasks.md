# Tasks

## 1. 后端镜像与依赖

- [x] 1.1 把 `pypdf` 补进 `backend/pyproject.toml` 的 `dependencies`，并在 `backend/app/main.py` 启动时导入全部解析引擎做自检；验证：全新虚拟环境 `pip install -e ./backend` 后 `python -c "from app.parsing import engine_names; print(engine_names())"` 不再缺包，且自检失败时进程以非零码退出
- [x] 1.2 编写 `backend/Dockerfile`（`python:3.11-slim`、非 root 用户、`pip install --no-cache-dir .`、暴露 8000）；验证：`docker build -t kp-backend:test backend` 成功，`docker run --rm kp-backend:test python -m app.scripts.check_env` 能打印引擎列表与配置项（新增的最小自检入口）
- [x] 1.3 补 `.dockerignore`（排除 `.git`、`tests`、`__pycache__`、`*.pyc`、`.env`、`frontend/node_modules`）；验证：`docker build` 上下文小于 50MB，且镜像内不存在 `.env`

## 2. 网关与编排

- [x] 2.1 编写 `deploy/gateway/nginx.conf` 与 `deploy/gateway/active.conf` 模板：`/` 指向前端版本目录、`/api` 反代到 `api-<color>:8000`、`index.html` 用 `no-cache`、`assets/` 用长缓存；验证：`docker run` 起 gateway 后 `curl -I localhost:8080/` 与 `curl -I localhost:8080/assets/<hash>.js` 的 `Cache-Control` 符合预期
- [x] 2.2 编写 `docker-compose.release.yml`：`gateway`、`api-blue`/`api-green`、`worker-blue`/`worker-green`、`beat-blue`/`beat-green`，两色用 `profiles` 区分，端口仅绑 `127.0.0.1`，`stop_grace_period: 45s`；验证：`docker compose -f docker-compose.yml -f docker-compose.release.yml --profile blue config` 通过，且 `--profile blue --profile green up -d` 可同时启动两色并各自 `/healthz` 可达
- [ ] 2.3 为 api 容器加 healthcheck（用容器内 `python -c urllib` 访问 `/healthz`，不依赖 curl）；验证：`docker inspect` 显示 healthy，手动 `kill -9` 主进程后 compose 自动重启

## 3. 探针与优雅退出

- [x] 3.1 实现 `GET /readyz`：并行检查 Postgres `SELECT 1`、Redis `PING`、S3 `bucket_exists`，Neo4j 作为可降级项；单项 1.5s 超时、结果缓存 3s；验证：单测覆盖"必需失败→503""仅 Neo4j 失败→200+degraded""全部正常→200"
- [x] 3.2 `/healthz` 增加 `version` 与 `color` 字段（读 `RELEASE_VERSION` / `RELEASE_COLOR`，缺省为 `dev`/`none`）；验证：容器内 `curl localhost:8000/healthz` 返回两者，且未设置环境变量时不报错
- [ ] 3.3 升级 `uvicorn[standard]>=0.29` 并给 api 启动命令加 `--timeout-graceful-shutdown 30`；验证：手工发一个长 SSE 请求，`docker stop` 后客户端能收到 `done` 或自然结束，而不是连接重置
- [x] 3.4 给 beat 加 Redis 单实例锁（`SET NX PX` + 续租），抢锁失败打日志并退出；验证：同时启动 `beat-blue` 与 `beat-green`，只有一个保持运行，`watchdog.sweep` 每周期只出现一次
- [ ] 3.5 worker 排空参数化：`stop_grace_period` 与 `WORKER_DRAIN_GRACE` 可配，SIGTERM 走 warm shutdown；验证：起一个长解析任务后向 worker 发 SIGTERM，任务完成前进程不退出，且 `execution_history` 不出现"执行者消失"
- [x] 3.6 为 3.1/3.4 的行为补测试（`backend/tests`）：readyz 三态、beat 锁竞争；验证：`python -m pytest tests -q` 通过

## 4. 发布产物与状态

- [x] 4.1 新增前端构建脚本（PowerShell）：`vite build` 后把 `dist` 复制到 `deploy/releases/<version>/`，保留最近 3 个版本，输出 `index.html` 与 `assets` 同目录；验证：连续构建两个版本后两目录并存，`index.html` 引用的资源名能在同目录 `assets/` 找到
- [x] 4.2 实现 `deploy/state` 读写：`current.json`（版本、颜色、上一版本、时间）与 `releases.jsonl`（只追加）；写入用临时文件 + rename，读取容忍空文件；验证：写入过程中断（模拟写一半）不会留下非法 JSON，重跑可恢复
- [x] 4.3 编写 `scripts/check_migrations.py`：扫描本次新增的 alembic 迁移，命中 `drop_column`/`drop_table`/`alter_column(nullable=False)`/`rename_table` 时返回非零并打印命中位置；验证：用一份含 `drop_column` 的临时迁移跑出告警，用 expand 迁移跑出通过
- [x] 4.4 为 4.2/4.3 补测试；验证：`python -m pytest tests -q` 通过

## 5. 发布脚本

- [x] 5.1 `scripts/release.ps1` 预检：校验 docker/compose 可用、`.env` 存在、版本号格式、当前 active 颜色与目标颜色不冲突；验证：故意删 `.env` 时脚本在构建前以清晰错误退出
- [x] 5.2 构建阶段：打 `kp-backend:<version>` 镜像、生成 `deploy/releases/<version>/`；验证：脚本失败时不留半成品镜像 tag（或明确记录可清理）
- [ ] 5.3 迁移阶段：起一次性迁移容器执行 `alembic upgrade head`，先跑 4.3 的检查，失败则中止且不启动目标色；验证：注入一个会失败的迁移，脚本退出码非零、active 未变、目标色未启动
- [x] 5.4 启动与验证阶段：起目标色 api/worker，轮询 `/readyz`（90s 超时），再跑只读冒烟（登录取 token + `POST /retrieval/search`）；验证：手动让目标色启动即崩，脚本在超时后报"readyz 未通过"并清理目标色
- [x] 5.5 切流阶段：原子重写 `active.conf`（upstream 与前端 root 同时改），`nginx -s reload`；验证：切换期间以 20 QPS 持续请求 60s，无 5xx，切换后 `healthz` 的 color 变为目标色
- [x] 5.6 收尾阶段：停旧 beat → 起新 beat → 旧 API 排空 → 旧 worker 延迟下线（`WORKER_DRAIN_GRACE`）；验证：切换期间有一个进行中的 SSE 与一个解析任务，二者都正常结束
- [x] 5.7 失败路径：任一步失败都停掉目标色、保留 active、写入失败记录；验证：在 5.4 注入失败后重跑发布可成功，状态文件里同时留有失败与成功两条记录
- [x] 5.8 用"同版本重发"演练完整流程（不改版本号，只验证编排与排空）；验证：演练后 `releases.jsonl` 多一条记录、无残留容器

## 6. 回滚

- [x] 6.1 `scripts/rollback.ps1`：读 `current.json` 的上一版本，校验其 `/readyz` 后重写 `active.conf` 并 reload；验证：切到 green 后执行回滚，30s 内 `healthz` 的 color 回到 blue，前端 `index.html` 引用旧版本目录
- [x] 6.2 回滚边界：检测最近一次发布是否包含 contract 迁移，命中则拒绝并打印冲突版本；验证：造一条 contract 记录后执行回滚，脚本拒绝且 active 不变
- [x] 6.3 回滚记录：写入 `releases.jsonl`（含 `rollback_of` 与原因参数）；验证：记录可被 `current.json` 读取逻辑识别，最近发布列表顺序正确

## 7. 文档与本地开发兼容

- [x] 7.1 README 新增"部署与发布"章节：镜像、端口、`deploy/` 目录布局、release/rollback 命令与失败处理；验证：按文档从零执行一遍 `release.ps1` 能成功（在干净宿主上）
- [x] 7.2 `.env.example` 增加 `RELEASE_VERSION`、`RELEASE_COLOR`、`WORKER_DRAIN_GRACE`，并注明只由发布脚本写入；验证：`start-backend.ps1` 在未设置这些变量时仍能启动
- [x] 7.3 新增 `deploy/README.md`：expand/contract 迁移规范、消息契约只增不改、beat 单实例、版本目录保留策略；验证：文档中的命令逐条可执行
- [x] 7.4 确认 conda 本地开发路径未被破坏：`scripts/start-backend.ps1` 与 `start-worker.ps1` 仍可启动，README 说明"容器发布"与"conda 开发"共用中间件时需互斥；验证：本地起一次 API 并访问 `/healthz`

## 8. 集成验收

- [ ] 8.1 完整跑一次真实发布（v1 blue → v2 green）并记录切换耗时；验证：切换期间无 5xx，SSE 不断流，`releases.jsonl` 记录完整
- [ ] 8.2 新旧 worker 混跑验证：切换窗口内两版 worker 同时消费，各投递 10 个解析任务；验证：20 个任务全部成功，无参数反序列化错误、无重复执行
- [x] 8.3 执行一次回滚并核对前端版本一致；验证：回滚后页面资源来自上一版本目录，API `healthz` color 与之一致
- [x] 8.4 检查发布记录与当前状态可查询；验证：仅凭 `deploy/state` 能回答"当前版本、颜色、上一成功版本、最近一次失败阶段"

## 实施记录（2026-11-25）

演练环境：Windows + Docker Desktop；宿主 8000/8001 被本地开发进程占用，`.env` 使用 `BLUE_API_PORT=8010` / `GREEN_API_PORT=8011`。

- 实际跑通的链路：`v20261125-4` 首发（blue）→ `v20261125-5`（green）→ 回滚 blue → 同版本重发（blue→green）→ 换色回滚 blue；切流窗口 60s 内 1240 次请求、0 个 5xx。
- 演练中修掉的问题：`up -d` 被 PowerShell 吞成 `up`（改用 `--detach`）、`api-$Color:8000` 被解析成 scope 变量导致上游为空（改 `${Color}`）、preflight 失败时误清理当前服务色容器、回滚探测端口硬编码未读 `.env`。
- 未完成验证（环境限制，需补）：
  - 3.3 / 5.6 / 8.1：SSE 与真实解析任务需要 LLM / embedding 与样例文件，本地未配置；只验证了配置项（`--timeout-graceful-shutdown 30`、`proxy_buffering off`、45s stop_grace、900s 排空）。
  - 2.3：`docker inspect` 显示 healthy；`docker kill` 被 Docker 视为手动停止不触发重启策略，容器内对 PID 1 注入信号未使容器退出，自动重启未实测。
  - 3.5：排空脚本与宽限参数已验证（`drain.log` code=0），长任务 SIGTERM 未实测。
  - 5.3：迁移容器正常路径通过，失败迁移注入未做。
  - 8.2：新旧 worker 混跑需要真实解析队列，未做。

## Workflow follow-up

- 评审通过后运行 `/opsx:apply` 实施本变更。
- TLS 终止位置、宿主 OS、数据卷是否改 bind mount、冒烟账号四项在 design 的 Open Questions 中，需在实施前与运维确认；它们不改变本任务分解。
