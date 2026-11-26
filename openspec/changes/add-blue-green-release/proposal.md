# Proposal

## Why

当前只有单一运行形态：API 与 Celery worker 直接跑在宿主机的 conda 环境里，前端靠 `npm run dev` 或一次性构建产物，中间件由 `docker-compose.yml` 提供。发布一个新版本等于"停旧进程、起新进程"，因此在发布窗口内必然出现三种损失：

- 正在流式作答的 `POST /api/v1/retrieval/ask`（SSE）直接断流，用户看到半截答案；
- 正在解析/抽取的长任务随 worker 被杀，只能依赖看护流程判定僵尸后重投，进度回退且留下错误记录；
- 新版本起不来（迁移写错、依赖缺失、配置漏项）时没有可用的上一版，只能现场排查，停机时间不可控。

把进程放进容器只是手段；真正要解决的是让"上一版始终活着、流量在验证后才切、出错能秒级切回"，并把"两版共存期间不出错"变成可检查的规则（数据库迁移、任务消息、定时任务、前端静态资源）。

## What Changes

- **容器化运行单元**：新增后端镜像（同一镜像承担 API / worker / beat / 迁移四种角色，靠启动命令区分）与 gateway（nginx 托管前端版本目录并反代 `/api`）；中间件仍由 compose 提供，前端不再需要独立进程。
- **双版本并存**：同一 `release` 版本号贯穿镜像 tag、前端版本目录与 `/healthz` 返回的 `version`；blue / green 两套应用容器可同时运行，共享同一套 Postgres / Redis / Neo4j / S3。
- **网关与原子切换**：新增 gateway（nginx）作为唯一入口，upstream 指向 blue 或 green；切换 = 重写 upstream 片段 + `nginx -s reload`，不重启容器、不断开已建立连接。
- **就绪探针与切换前验证**：新增 `/readyz` 检查数据库、Redis、对象存储、Neo4j 连通性；`/healthz` 保持轻量仅作存活判定。切换脚本先等目标颜色 `readyz` 通过并跑只读冒烟，失败则不切。
- **优雅排空**：旧颜色收到 SIGTERM 后停止接新请求，允许存量 SSE 流走完；worker 走 warm shutdown 等当前任务结束再退出；超时上限后强制停止。
- **前端版本目录**：构建产物落到 `releases/<version>/`，`current` 指向当前版本；切换与回滚只改指向，不覆盖上一版本目录。
- **发布与回滚脚本**：`scripts/release.ps1` / `scripts/rollback.ps1` 串起"构建 → 迁移 → 起新色 → 验证 → 切流 → 排空旧色"，并把版本、颜色、操作结果写入发布记录，供回滚与审计读取。
- **跨版本兼容纪律**：数据库迁移拆成 expand（向后兼容，发布时执行）与 contract（下一版清理）；Celery 任务参数只增不改语义；beat 只允许 active 颜色持有；回滚边界明确为"未执行 contract 才能回滚"。
- **BREAKING**：部署方式从"宿主机 conda + 端口"改为"容器 + 网关"。容器内 `DATABASE_URL` 等指向 compose 服务名而非 `localhost`，宿主 `conda` 启动方式退化为本地开发路径，不再作为发布手段。

## Capabilities

### New Capabilities

- `blue-green-release`: 版本化发布单元、双版本并存、网关原子切流、就绪探针与切换前验证、连接排空、回滚、前端版本目录、跨版本数据与消息兼容、发布记录审计。

### Modified Capabilities

无。现有 `extraction-checkpoint`、`task-retry-and-recovery` 等能力描述的是任务执行语义，本次不改变其对外行为；蓝绿只改变"进程如何被替换"，与看护/重试的交互在 design 中约束。

## Impact

- **新增文件**：`backend/Dockerfile`、`docker-compose.release.yml`、`deploy/gateway/`（nginx 配置与 `active.conf` 模板）、`deploy/releases/`（前端版本目录）、`deploy/state/`（发布记录）、`scripts/release.ps1`、`scripts/rollback.ps1`、`scripts/check_migrations.py`、`.dockerignore`。
- **后端改动**：`app/main.py` 增加 `/readyz` 与优雅退出；`app/core/config.py` 增加 `release_version` / `release_color`；`celery_app.py` 与 worker 启动命令对齐 warm shutdown 参数。
- **迁移约定**：`alembic/versions/` 新增 expand/contract 命名与校验约定；发布脚本只执行 expand 阶段。
- **前端改动**：构建输出改为版本目录并按目录托管；`vite.config.ts` 的 dev 代理保持不变。
- **文档**：README 增加部署与回滚章节；`.env.example` 增加发布相关变量（颜色、版本、外部入口）。
- **不影响**：业务 API 契约、数据库表结构语义、现有 OpenSpec 能力的对外行为。
