"""改写实验室的三组真实对照：关闭改写 / 只开语义改写 / 全开。

脚本做三件事：
1. 临时插入一小批已发布语料（用真实 embedding 向量，保证可检索）；
2. 用同一批问题依次跑三组配置，收集 Trace；
3. 输出 Markdown 对照表与原始 Trace JSON，结束后清理语料（--keep-corpus 可保留）。

跑在真实模型上会产生调用费用，按需执行（在 ``backend`` 目录下）：

    python -m scripts.query_rewrite_benchmark
"""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.infra.db.models import AppUser, Document, DocumentVersion, ParseStrategy
from app.infra.db.models.parse_task import ParseTask
from app.infra.db.models.retrieval import DocumentChunk
from app.infra.db.session import SessionLocal
from app.infra.llm import provider
from app.main import create_app

CORPUS: list[tuple[str, str]] = [
    (
        "年假管理制度",
        "员工入职满一年享有 5 天年假；累计工龄满十年享有 10 天年假；年假需提前三个工作日申请。",
    ),
    (
        "调休与加班规定",
        "工作日加班可按 1:1 申请调休；调休需在加班后三个月内使用，逾期作废，不可折算现金。",
    ),
    (
        "差旅报销标准",
        "出差住宿一线城市每晚不超过 600 元；差旅报销需在行程结束后 30 天内提交发票。",
    ),
    (
        "病假与事假规定",
        "病假需提供二级以上医院证明；事假按天扣发当日工资，全年事假累计不超过 15 天。",
    ),
    (
        "新员工入职指引",
        "新员工入职需提交身份证、学历证明与离职证明；入职当月即缴纳社保与公积金。",
    ),
    (
        "考勤与请假流程",
        "请假需在考勤系统提交申请，由直属主管审批；连续请假超过三天需部门负责人复核。",
    ),
]

QUESTIONS: list[dict[str, Any]] = [
    {"id": "q1", "query": "它怎么算？", "history": [{"role": "user", "content": "年假怎么算"}]},
    {"id": "q2", "query": "年假和调休有啥不一样？", "history": []},
    {
        "id": "q3",
        "query": "出差回来报销和年假没休完折算这两件事分别怎么弄？",
        "history": [],
    },
    {"id": "q4", "query": "《员工手册》里请假的规定？", "history": []},
    {"id": "q5", "query": "满十年工龄年假多少天？", "history": []},
]

CONFIGS: list[dict[str, Any]] = [
    {"key": "off", "label": "关闭改写", "allowlist": [], "budget": 1},
    {"key": "semantic", "label": "只开语义改写", "allowlist": ["semantic"], "budget": 2},
    {
        "key": "full",
        "label": "全开",
        "allowlist": ["context", "entity", "semantic", "decompose", "multi"],
        "budget": 5,
    },
]


def seed_corpus() -> list[uuid.UUID]:
    """插入已发布文档与片段；返回文档 id，供结束时清理。"""
    session = SessionLocal()
    created: list[uuid.UUID] = []
    try:
        admin = (
            session.query(AppUser)
            .filter(AppUser.username == settings.seed_admin_username)
            .one()
        )
        strategy_id = session.scalar(select(ParseStrategy.id).limit(1))
        if strategy_id is None:
            raise SystemExit("没有解析策略种子，无法造语料")

        vectors = provider.embed_texts([title + " " + content for title, content in CORPUS])
        for (title, content), vector in zip(CORPUS, vectors, strict=True):
            document = Document(
                workspace_id=admin.workspace_id,
                title=f"{title}（benchmark）",
                doc_type="text",
                status="published",
                sensitivity="internal",
                created_by=admin.id,
                owner_id=admin.id,
            )
            session.add(document)
            session.flush()

            version = DocumentVersion(
                document_id=document.id,
                version_no=1,
                storage_key=f"benchmark/{uuid.uuid4().hex}.txt",
                file_name=f"{title}.txt",
                file_size=len(content.encode("utf-8")),
                mime_type="text/plain",
                checksum=uuid.uuid4().hex,
                is_current=True,
                created_by=admin.id,
            )
            session.add(version)
            session.flush()
            document.current_version_id = version.id

            task = ParseTask(
                workspace_id=document.workspace_id,
                document_id=document.id,
                document_version_id=version.id,
                strategy_id=strategy_id,
                status="succeeded",
                created_by=admin.id,
            )
            session.add(task)
            session.flush()

            session.add(
                DocumentChunk(
                    workspace_id=document.workspace_id,
                    document_id=document.id,
                    document_version_id=version.id,
                    parse_task_id=task.id,
                    seq=0,
                    content=content,
                    char_count=len(content),
                    embedding=list(vector),
                    embedding_model=settings.embedding_model,  # 与库中列定义一致
                )
            )
            created.append(document.id)
        session.commit()
    finally:
        session.close()
    return created


def drop_corpus(document_ids: list[uuid.UUID]) -> None:
    session = SessionLocal()
    try:
        for document_id in document_ids:
            document = session.get(Document, document_id)
            if document is not None:
                session.delete(document)
        session.commit()
    finally:
        session.close()


def run_config(
    client: TestClient,
    headers: dict[str, str],
    config: dict[str, Any],
    question: dict[str, Any],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "query": question["query"],
        "mode": "auto",
        "allowlist": config["allowlist"],
        "budget": config["budget"],
        "quality_threshold": 0.6,
        "top_k": 5,
    }
    if question["history"]:
        payload["history"] = question["history"]
    resp = client.post("/api/v1/retrieval/rewrite-lab/run", json=payload, headers=headers)
    if resp.status_code != 200:
        raise SystemExit(f"运行失败（{config['label']} / {question['id']}）：{resp.text}")
    return resp.json()["data"]


def summarize(trace: dict[str, Any]) -> dict[str, Any]:
    arms = {item["arm"]: item for item in trace["arms"]}
    executed = [item["strategy"] for item in trace["routing"] if item["decision"] == "execute"]
    # 改写组没跑就没有增益可言，别把 evaluation 里的 0 当成"打平"
    rewritten_executed = bool(arms["rewritten"]["queries"])
    return {
        "executed": executed,
        "rewritten_executed": rewritten_executed,
        "baseline_top1": arms["baseline"]["metrics"]["top1"],
        "rewritten_top1": arms["rewritten"]["metrics"]["top1"],
        "lift": trace["evaluation"]["lift"] if rewritten_executed else None,
        "above_tau": arms["rewritten"]["metrics"]["above_tau"],
        "docs": arms["rewritten"]["metrics"]["docs"],
        "verdict": trace["evaluation"]["verdict"],
        "elapsed_ms": trace["totals"]["elapsed_ms"],
        "llm_calls": trace["totals"]["llm_calls"],
        "retrieval_calls": trace["totals"]["retrieval_calls"],
        "warnings": len(trace["warnings"]),
        "degraded": trace["degraded"],
    }


def render_markdown(rows: list[dict[str, Any]]) -> str:
    header = (
        "| 问题 | 配置 | top1 对照 | top1 改写 | lift | 达标片段 | 覆盖文档 "
        "| 结论 | 耗时 ms | 模型调用 | 提示 |"
    )
    lines = [header, "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for row in rows:
        lift = row["summary"]["lift"]
        lines.append(
            "| {question} | {config} | {baseline:.4f} | {rewritten:.4f} | {lift} | "
            "{above_tau} | {docs} | {verdict} | {elapsed} | {llm} | {warnings} |".format(
                question=row["question"],
                config=row["config_label"],
                baseline=row["summary"]["baseline_top1"],
                rewritten=row["summary"]["rewritten_top1"],
                lift="未完成" if lift is None else f"{lift:+.4f}",
                above_tau=row["summary"]["above_tau"],
                docs=row["summary"]["docs"],
                verdict=row["summary"]["verdict"],
                elapsed=row["summary"]["elapsed_ms"],
                llm=row["summary"]["llm_calls"],
                warnings=row["summary"]["warnings"],
            )
        )
    lines.append("")
    for config in CONFIGS:
        subset = [row for row in rows if row["config"] == config["key"]]
        if not subset:
            continue
        lifts = [row["summary"]["lift"] for row in subset if row["summary"]["lift"] is not None]
        mean_lift = sum(lifts) / len(lifts) if lifts else 0.0
        mean_elapsed = sum(row["summary"]["elapsed_ms"] for row in subset) / len(subset)
        mean_llm = sum(row["summary"]["llm_calls"] for row in subset) / len(subset)
        lines.append(
            f"- {config['label']}：平均 lift {mean_lift:+.4f}"
            f"（{len(lifts)}/{len(subset)} 条完成改写组），"
            f"平均耗时 {mean_elapsed:.0f} ms，平均模型调用 {mean_llm:.1f} 次"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="改写实验室三组对照")
    parser.add_argument("--out", type=Path, default=Path("rewrite-benchmark.json"))
    parser.add_argument("--keep-corpus", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="只检查配置，不调用模型")
    args = parser.parse_args()

    if not provider.is_embedding_configured() or not provider.is_llm_configured():
        raise SystemExit("未配置 embedding 或 LLM，先在 .env 中补齐再运行")
    if args.dry_run:
        print(render_markdown([]))
        return

    app = create_app()
    rows: list[dict[str, Any]] = []
    traces: list[dict[str, Any]] = []
    document_ids: list[uuid.UUID] = []
    try:
        document_ids = seed_corpus()
        print(f"已造语料 {len(document_ids)} 篇")
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/auth/login",
                json={
                    "username": settings.seed_admin_username,
                    "password": settings.seed_admin_password,
                },
            )
            if resp.status_code != 200:
                raise SystemExit(f"登录失败：{resp.text}")
            headers = {"Authorization": f"Bearer {resp.json()['data']['access_token']}"}

            for config in CONFIGS:
                for question in QUESTIONS:
                    trace = run_config(client, headers, config, question)
                    summary = summarize(trace)
                    rows.append(
                        {
                            "question": question["id"],
                            "query": question["query"],
                            "config": config["key"],
                            "config_label": config["label"],
                            "summary": summary,
                        }
                    )
                    traces.append(
                        {"config": config["key"], "question": question["id"], "trace": trace}
                    )
                    lift = summary["lift"]
                    print(
                        f"{config['key']:<9} {question['id']} "
                        f"lift={'未完成' if lift is None else f'{lift:+.4f}'} "
                        f"top1={summary['baseline_top1']:.4f}/{summary['rewritten_top1']:.4f} "
                        f"{summary['elapsed_ms']}ms"
                    )
    finally:
        if document_ids and not args.keep_corpus:
            drop_corpus(document_ids)
            print(f"已清理语料 {len(document_ids)} 篇")

    markdown = render_markdown(rows)
    print("\n" + markdown)
    args.out.write_text(
        json.dumps({"rows": rows, "traces": traces}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n原始 Trace 已写入 {args.out.resolve()}")


if __name__ == "__main__":
    main()
