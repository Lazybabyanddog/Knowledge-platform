# 单镜像四角色：api / worker / beat / migrate 共用同一份代码，
# 启动命令由 compose 覆盖。保证"发布时跑的迁移"与"切流后跑的代码"完全一致。
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 依赖层先于源码层：改业务代码不会让 pip install 重跑
COPY pyproject.toml ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

# 慢网下 pip 默认的 15s 读超时会直接 ReadTimeout 失败：
# 加大重试与超时，并允许构建时注入镜像源（release.ps1 会透传 PIP_INDEX_URL / PIP_EXTRA_INDEX_URL）
ARG PIP_INDEX_URL=
ARG PIP_EXTRA_INDEX_URL=
RUN pip install --no-cache-dir --retries 10 --timeout 120 \
        ${PIP_INDEX_URL:+--index-url ${PIP_INDEX_URL}} \
        ${PIP_EXTRA_INDEX_URL:+--extra-index-url ${PIP_EXTRA_INDEX_URL}} . \
    && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

# 默认命令只做自检：镜像能不能用，一跑就知道，而不是等第一个上传请求
CMD ["python", "-m", "app.scripts.check_env"]
