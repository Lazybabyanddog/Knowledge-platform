"""问答留痕、用户反馈与低置信度分诊。

三段职责写在一个模块里，是因为它们共用同一条"记录 → 判定 → 入队"链条：

- ``QaService``：问答记录的开立与终态回填、用户评价
- ``QaTriageService``：入队、归簇、处置动作与轨迹
- 终态回填走独立会话：SSE 流期间请求会话已随依赖退出而关闭，不能复用
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import Principal
from app.core.exceptions import BadRequestError, NotFoundError
from app.domain.enums import (
    AuditAction,
    QaAnswerStatus,
    QaDisposition,
    QaIssueAction,
    QaIssueStatus,
    QaRating,
    QaReason,
    UserStatus,
)
from app.domain.qa_triage import (
    AUTO_REASONS,
    compose,
    evaluate,
    has_hard_reason,
    order_reasons,
    primary_source,
)
from app.domain.visibility import allowed_sensitivities, highest_sensitivity
from app.infra.db.models import AppUser, Document
from app.infra.db.models.qa import QaFeedback, QaIssue, QaIssueEvent, QaIssueRecord, QaRecord
from app.infra.db.repositories.qa_repository import QaRepository
from app.infra.db.session import session_scope
from app.services.audit_service import AuditService
from app.services.retrieval_service import ChunkHit, hit_to_source

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


def _score_of(hits: list[ChunkHit]) -> tuple[float | None, float | None]:
    if not hits:
        return None, None
    return max(h.score for h in hits), min(h.score for h in hits)


class QaService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = QaRepository(db)
        self.audit = AuditService(db)

    # ---------------- 记录 ----------------

    def open_record(
        self,
        principal: Principal,
        question: str,
        hits: list[ChunkHit],
        question_vec: list[float] | None,
    ) -> QaRecord:
        """流开始前落一条 streaming 记录，标识随 ``done`` 事件下发。"""
        snapshots = self._snapshot_sources(hits)
        top_score, last_score = _score_of(hits)
        record = QaRecord(
            workspace_id=principal.user.workspace_id,
            user_id=principal.user_id,
            user_name=principal.user.display_name,
            question=question,
            question_vec=question_vec or None,
            answer="",
            answer_status=QaAnswerStatus.STREAMING.value,
            sources=snapshots,
            hit_count=len(hits),
            top_score=top_score,
            last_score=last_score,
            reasons=[],
            # 整条记录继承来源的最高密级：正文往往会复述片段，不能半遮挡
            sensitivity=highest_sensitivity([s["sensitivity"] for s in snapshots]),
            started_at=_now(),
        )
        return self.repo.add_record(record)

    def _snapshot_sources(self, hits: list[ChunkHit]) -> list[dict[str, Any]]:
        if not hits:
            return []
        doc_ids = {uuid.UUID(hit.document_id) for hit in hits}
        rows = self.db.execute(
            select(Document.id, Document.sensitivity).where(Document.id.in_(doc_ids))
        ).all()
        sensitivity_by_doc = {str(row[0]): row[1] for row in rows}
        sources: list[dict[str, Any]] = []
        for index, hit in enumerate(hits, start=1):
            source = hit_to_source(hit, index)
            source["sensitivity"] = sensitivity_by_doc.get(hit.document_id, "public")
            sources.append(source)
        return sources

    @staticmethod
    def finalize(
        record_id: uuid.UUID,
        answer: str,
        status: QaAnswerStatus,
        elapsed_ms: int,
    ) -> None:
        """流结束后用独立事务落终态；失败只记日志，不影响已推给前端的答案。"""
        try:
            with session_scope() as db:
                QaService(db).finalize_in_session(record_id, answer, status, elapsed_ms)
        except Exception:  # pragma: no cover - 留痕失败不应反向打断响应
            logger.warning("问答记录回填失败 record=%s", record_id, exc_info=True)

    def finalize_in_session(
        self, record_id: uuid.UUID, answer: str, status: QaAnswerStatus, elapsed_ms: int
    ) -> None:
        record = self.repo.get_record_for_update(record_id)
        if record is None or record.answer_status != QaAnswerStatus.STREAMING.value:
            return
        record.answer = answer
        record.answer_status = status.value
        record.elapsed_ms = elapsed_ms
        record.finished_at = _now()

        if not settings.qa_triage_enabled:
            # 灰度期只留痕：不算原因、不建簇，后续可按同一套规则重算
            record.reasons = []
            return

        auto = evaluate(
            hit_count=record.hit_count,
            top_score=record.top_score,
            last_score=record.last_score,
            answer=answer,
            low_hit_floor=settings.qa_low_hit_floor,
            score_mark_threshold=settings.qa_score_mark_threshold,
            score_gap_threshold=settings.qa_score_gap_threshold,
        )
        record.reasons = compose(auto, disliked=QaReason.USER_DISLIKE.value in record.reasons)

        # 中断与失败的记录不进队列：答案不完整，无法据此判断是缺口还是幻觉
        if status == QaAnswerStatus.COMPLETED and has_hard_reason(record.reasons):
            QaTriageService(self.db).enqueue(record)

    # ---------------- 反馈 ----------------

    def _own_record(self, principal: Principal, record_id: uuid.UUID) -> QaRecord:
        record = self.repo.get_record(record_id)
        # 他人记录与不存在记录返回同一个错误，避免用标识探测别人问过什么
        if (
            record is None
            or record.workspace_id != principal.user.workspace_id
            or record.user_id != principal.user_id
        ):
            raise NotFoundError("问答记录不存在")
        return record

    def get_feedback(self, principal: Principal, record_id: uuid.UUID) -> QaFeedback | None:
        self._own_record(principal, record_id)
        return self.repo.get_feedback(record_id, principal.user_id)

    def submit_feedback(
        self,
        principal: Principal,
        record_id: uuid.UUID,
        *,
        rating: str,
        reason: str | None,
        note: str | None,
        ip: str | None = None,
    ) -> QaFeedback:
        self._own_record(principal, record_id)
        record = self.repo.get_record_for_update(record_id)
        if record is None:  # pragma: no cover - 上面刚校验过
            raise NotFoundError("问答记录不存在")
        if record.answer_status != QaAnswerStatus.COMPLETED.value:
            raise BadRequestError("该问答未正常完成，暂不能评价")
        if rating not in {QaRating.LIKE.value, QaRating.DISLIKE.value}:
            raise BadRequestError("评价取值不合法")

        disliked = rating == QaRating.DISLIKE.value
        if disliked:
            if not reason:
                raise BadRequestError("请选择不满意的原因")
            if reason == "other" and not (note or "").strip():
                raise BadRequestError("选择“其他”时请填写补充说明")
        else:
            reason, note = None, None

        feedback = self.repo.get_feedback(record_id, principal.user_id)
        if feedback is None:
            feedback = QaFeedback(
                record_id=record_id,
                user_id=principal.user_id,
                rating=rating,
                reason=reason,
                note=note,
            )
            self.repo.add_feedback(feedback)
        else:
            feedback.rating = rating
            feedback.reason = reason
            feedback.note = note
            feedback.updated_at = _now()

        if settings.qa_triage_enabled:
            # 用户原因在自动原因之外单独合成：改评价不应抹掉检索侧的标记
            auto = [r for r in (record.reasons or []) if r in AUTO_REASONS]
            record.reasons = compose(auto, disliked=disliked)

            triage = QaTriageService(self.db)
            if disliked:
                triage.enqueue(record)
            elif not has_hard_reason(record.reasons):
                triage.unlink(record)

        self.audit.record(
            AuditAction.QA_FEEDBACK,
            actor=principal.user,
            object_type="qa_record",
            object_id=record_id,
            after={"rating": rating, "reason": reason},
            ip=ip,
        )
        return feedback


class QaTriageService:
    """问题簇的入队、归并与处置；鉴权由接口层的 ``qa:manage`` 依赖完成。"""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = QaRepository(db)
        self.audit = AuditService(db)

    # ---------------- 入队与归簇 ----------------

    def enqueue(self, record: QaRecord) -> QaIssue | None:
        if record.answer_status != QaAnswerStatus.COMPLETED.value:
            return None
        if not has_hard_reason(record.reasons):
            return None

        existing = self.repo.get_membership(record.id)
        if existing is not None:
            issue = self.repo.get_issue(existing.issue_id)
            if issue is not None:
                # 评价改判后再次点踩：计数要跟着变，但不要重复建簇
                self._refresh_counters(issue)
            return issue

        issue = self._match_issue(record) or self._create_issue(record)
        self.repo.add_membership(issue.id, record.id)
        self._refresh_counters(issue)
        return issue

    def unlink(self, record: QaRecord) -> None:
        """记录失去全部硬信号后从簇里摘掉；簇空了就一并删除。"""
        link = self.repo.get_membership(record.id)
        if link is None:
            return
        issue = self.repo.get_issue(link.issue_id)
        self.repo.delete_membership(link)
        if issue is None:
            return
        self._refresh_counters(issue)
        if issue.record_count <= 0:
            self.db.delete(issue)

    def _match_issue(self, record: QaRecord) -> QaIssue | None:
        source = primary_source(record.sources)
        auto = {r for r in (record.reasons or []) if r in AUTO_REASONS}
        # 先看"同一文档 + 同一触发原因"的精确匹配，弱化向量阈值的影响
        document_id = _document_id_of(source)
        if document_id is not None:
            for issue in self.repo.list_pending_issues_exact(
                record.workspace_id, record.sensitivity, document_id
            ):
                issue_auto = {r for r in (issue.reasons or []) if r in AUTO_REASONS}
                if issue_auto == auto:
                    return issue

        if record.question_vec is None:
            return None
        # 余弦距离 = 1 - 相似度，与配置里的相似度口径对齐
        max_distance = max(0.0, 1.0 - settings.qa_cluster_similarity)
        return self.repo.find_nearest_pending_issue(
            record.workspace_id,
            record.sensitivity,
            list(record.question_vec),
            max_distance=max_distance,
        )

    def _create_issue(self, record: QaRecord) -> QaIssue:
        when = record.finished_at or record.created_at or _now()
        issue = QaIssue(
            workspace_id=record.workspace_id,
            sensitivity=record.sensitivity,
            representative_question=record.question,
            representative_vec=record.question_vec,
            primary_document_id=_document_id_of(primary_source(record.sources)),
            reasons=list(record.reasons or []),
            record_count=0,
            dislike_count=0,
            first_seen_at=when,
            last_seen_at=when,
            status=QaIssueStatus.PENDING.value,
        )
        return self.repo.add_issue(issue)

    def _refresh_counters(self, issue: QaIssue) -> None:
        """成员变动后重算计数与原因并集；簇规模很小，回表比维护增量更不容易错。"""
        rows = self.db.execute(
            select(QaRecord.reasons, QaRecord.finished_at, QaRecord.created_at)
            .join(QaIssueRecord, QaIssueRecord.record_id == QaRecord.id)
            .where(QaIssueRecord.issue_id == issue.id)
        ).all()
        reasons: set[str] = set()
        dislike_count = 0
        last_seen: datetime | None = None
        for record_reasons, finished_at, created_at in rows:
            current = set(record_reasons or [])
            reasons |= current
            if QaReason.USER_DISLIKE.value in current:
                dislike_count += 1
            stamp = finished_at or created_at
            if stamp is not None and (last_seen is None or stamp > last_seen):
                last_seen = stamp
        issue.record_count = len(rows)
        issue.dislike_count = dislike_count
        issue.reasons = order_reasons(reasons)
        if last_seen is not None:
            issue.last_seen_at = last_seen

    # ---------------- 查询 ----------------

    def list_issues(self, principal: Principal, *, page: int = 1, page_size: int = 20, **filters: Any) -> tuple[list[dict[str, Any]], int]:
        issues, total = self.repo.list_issues(
            workspace_id=principal.user.workspace_id,
            sensitivities=allowed_sensitivities(principal),
            page=page,
            page_size=page_size,
            **filters,
        )
        items = [self._issue_item(issue) for issue in issues]
        self._fill_issue_extras(items)
        return items, total

    def list_assignees(self, principal: Principal) -> list[tuple[uuid.UUID, str, str]]:
        """本空间内可被指派的人；跨空间指派在服务层也会被拒绝。"""
        rows = self.db.execute(
            select(AppUser.id, AppUser.username, AppUser.display_name)
            .where(
                AppUser.workspace_id == principal.user.workspace_id,
                AppUser.status == UserStatus.ACTIVE.value,
            )
            .order_by(AppUser.display_name)
        ).all()
        return [(row[0], row[1], row[2]) for row in rows]

    def get_detail(self, principal: Principal, issue_id: uuid.UUID) -> dict[str, Any]:
        issue = self._visible_issue(principal, issue_id)
        allowed = allowed_sensitivities(principal)
        records = self.repo.list_member_records(issue.id, allowed)
        events = self.repo.list_events(issue.id)
        detail = self._issue_item(issue)
        detail["record_count"] = self.repo.count_member_records(issue.id, allowed)
        detail["records"] = [self._record_item(record) for record in records]
        detail["events"] = [self._event_item(event) for event in events]
        self._fill_issue_extras([detail])
        return detail

    def _issue_item(self, issue: QaIssue) -> dict[str, Any]:
        return {
            "id": str(issue.id),
            "representative_question": issue.representative_question,
            "record_count": issue.record_count,
            "dislike_count": issue.dislike_count,
            "reasons": list(issue.reasons or []),
            "sensitivity": issue.sensitivity,
            "status": issue.status,
            "disposition": issue.disposition,
            "assignee_id": str(issue.assignee_id) if issue.assignee_id else None,
            "assignee_name": None,
            "primary_document_id": (
                str(issue.primary_document_id) if issue.primary_document_id else None
            ),
            "primary_document_title": None,
            "hit_count": None,
            "first_seen_at": issue.first_seen_at,
            "last_seen_at": issue.last_seen_at,
            "created_at": issue.created_at,
        }

    def _fill_issue_extras(self, items: list[dict[str, Any]]) -> None:
        """一次性补齐处理人、来源文档标题与代表性命中数，避免列表 N+1。"""
        if not items:
            return
        issue_ids = [uuid.UUID(item["id"]) for item in items]
        user_ids = {uuid.UUID(item["assignee_id"]) for item in items if item["assignee_id"]}
        document_ids = {
            uuid.UUID(item["primary_document_id"])
            for item in items
            if item["primary_document_id"]
        }

        names: dict[uuid.UUID, str] = {}
        if user_ids:
            rows = self.db.execute(
                select(AppUser.id, AppUser.display_name).where(AppUser.id.in_(user_ids))
            ).all()
            names = {row[0]: row[1] for row in rows}

        titles: dict[uuid.UUID, str] = {}
        if document_ids:
            rows = self.db.execute(
                select(Document.id, Document.title).where(Document.id.in_(document_ids))
            ).all()
            titles = {row[0]: row[1] for row in rows}

        hit_counts = self._representative_hit_counts(issue_ids)

        for item in items:
            if item["assignee_id"]:
                item["assignee_name"] = names.get(uuid.UUID(item["assignee_id"]))
            if item["primary_document_id"]:
                item["primary_document_title"] = titles.get(
                    uuid.UUID(item["primary_document_id"])
                )
            item["hit_count"] = hit_counts.get(uuid.UUID(item["id"]))

    def _representative_hit_counts(self, issue_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
        if not issue_ids:
            return {}
        rows = self.db.execute(
            select(QaIssueRecord.issue_id, func.max(QaRecord.hit_count))
            .join(QaRecord, QaRecord.id == QaIssueRecord.record_id)
            .where(QaIssueRecord.issue_id.in_(issue_ids))
            .group_by(QaIssueRecord.issue_id)
        ).all()
        return {row[0]: int(row[1] or 0) for row in rows}

    def _record_item(self, record: QaRecord) -> dict[str, Any]:
        answer = record.answer or ""
        return {
            "id": str(record.id),
            "question": record.question,
            "answer_excerpt": answer[:500],
            "answer_status": record.answer_status,
            "reasons": list(record.reasons or []),
            "sensitivity": record.sensitivity,
            "user_name": record.user_name,
            "hit_count": record.hit_count,
            "top_score": record.top_score,
            "sources": list(record.sources or []),
            "created_at": record.created_at,
            "finished_at": record.finished_at,
        }

    def _event_item(self, event: QaIssueEvent) -> dict[str, Any]:
        return {
            "id": str(event.id),
            "action": event.action,
            "actor_name": event.actor_name,
            "note": event.note,
            "detail": dict(event.detail or {}),
            "created_at": event.created_at,
        }

    # ---------------- 处置 ----------------

    def assign(
        self,
        principal: Principal,
        issue_id: uuid.UUID,
        assignee_id: uuid.UUID | None,
        note: str | None = None,
        *,
        ip: str | None = None,
    ) -> QaIssue:
        issue = self._visible_issue(principal, issue_id)
        assignee: AppUser | None = None
        if assignee_id is not None:
            assignee = self.db.get(AppUser, assignee_id)
            if (
                assignee is None
                or assignee.workspace_id != principal.user.workspace_id
                or assignee.status != UserStatus.ACTIVE.value
            ):
                raise BadRequestError("处理人不在本空间或已停用")
        issue.assignee_id = assignee.id if assignee else None
        self._record_event(
            issue,
            principal,
            QaIssueAction.ASSIGN,
            note=note,
            detail={
                "assignee_id": str(assignee.id) if assignee else None,
                "assignee_name": assignee.display_name if assignee else None,
            },
        )
        self.audit.record(
            AuditAction.QA_ISSUE_ASSIGN,
            actor=principal.user,
            object_type="qa_issue",
            object_id=issue.id,
            after={"assignee_id": str(assignee.id) if assignee else None},
            ip=ip,
        )
        return issue

    def dispose(
        self,
        principal: Principal,
        issue_id: uuid.UUID,
        disposition: str,
        note: str | None = None,
        *,
        ip: str | None = None,
    ) -> QaIssue:
        issue = self._visible_issue(principal, issue_id)
        _ensure_disposition(disposition)
        issue.disposition = disposition
        self._record_event(
            issue,
            principal,
            QaIssueAction.DISPOSE,
            note=note,
            detail={"disposition": disposition},
        )
        self.audit.record(
            AuditAction.QA_ISSUE_DISPOSE,
            actor=principal.user,
            object_type="qa_issue",
            object_id=issue.id,
            after={"disposition": disposition},
            ip=ip,
        )
        return issue

    def close(
        self,
        principal: Principal,
        issue_id: uuid.UUID,
        disposition: str | None = None,
        note: str | None = None,
        *,
        ip: str | None = None,
    ) -> QaIssue:
        issue = self._visible_issue(principal, issue_id)
        final_disposition = disposition or issue.disposition
        # 关闭必须带结论：没有结论的关闭统计不出误报率，也没法验收
        if not final_disposition:
            raise BadRequestError("关闭前必须给出处置结论")
        _ensure_disposition(final_disposition)
        issue.disposition = final_disposition
        issue.status = QaIssueStatus.CLOSED.value
        self._record_event(
            issue,
            principal,
            QaIssueAction.CLOSE,
            note=note,
            detail={"disposition": final_disposition},
        )
        self.audit.record(
            AuditAction.QA_ISSUE_CLOSE,
            actor=principal.user,
            object_type="qa_issue",
            object_id=issue.id,
            after={"disposition": final_disposition, "status": issue.status},
            ip=ip,
        )
        return issue

    def reopen(
        self,
        principal: Principal,
        issue_id: uuid.UUID,
        note: str | None = None,
        *,
        ip: str | None = None,
    ) -> QaIssue:
        issue = self._visible_issue(principal, issue_id)
        issue.status = QaIssueStatus.PENDING.value
        self._record_event(
            issue,
            principal,
            QaIssueAction.REOPEN,
            note=note,
            detail={"disposition": issue.disposition},
        )
        self.audit.record(
            AuditAction.QA_ISSUE_REOPEN,
            actor=principal.user,
            object_type="qa_issue",
            object_id=issue.id,
            after={"status": issue.status},
            ip=ip,
        )
        return issue

    def _record_event(
        self,
        issue: QaIssue,
        principal: Principal,
        action: QaIssueAction,
        *,
        note: str | None,
        detail: dict[str, Any],
    ) -> QaIssueEvent:
        return self.repo.add_event(
            QaIssueEvent(
                issue_id=issue.id,
                action=action.value,
                actor_id=principal.user_id,
                actor_name=principal.user.display_name,
                note=note,
                detail=detail,
            )
        )

    def _visible_issue(self, principal: Principal, issue_id: uuid.UUID) -> QaIssue:
        issue = self.repo.get_issue(issue_id)
        if (
            issue is None
            or issue.workspace_id != principal.user.workspace_id
            or issue.sensitivity not in allowed_sensitivities(principal)
        ):
            # 不可见与不存在同码返回，避免用它探测别的密级有没有问题
            raise NotFoundError("问题簇不存在")
        return issue

    # ---------------- 阈值重算 ----------------

    def reevaluate(self, workspace_id: uuid.UUID, sensitivities: list[str]) -> int:
        """按当前阈值重算自动原因；用户原因原样保留。供运维脚本调用。"""
        changed = 0
        for record in self.repo.list_records_for_reeval(workspace_id, sensitivities):
            auto = evaluate(
                hit_count=record.hit_count,
                top_score=record.top_score,
                last_score=record.last_score,
                answer=record.answer or "",
                low_hit_floor=settings.qa_low_hit_floor,
                score_mark_threshold=settings.qa_score_mark_threshold,
                score_gap_threshold=settings.qa_score_gap_threshold,
            )
            disliked = QaReason.USER_DISLIKE.value in (record.reasons or [])
            new_reasons = compose(auto, disliked=disliked)
            if new_reasons != list(record.reasons or []):
                record.reasons = new_reasons
                changed += 1
            if settings.qa_triage_enabled and has_hard_reason(new_reasons):
                self.enqueue(record)
            else:
                self.unlink(record)
        return changed


def _document_id_of(source: dict[str, Any] | None) -> uuid.UUID | None:
    if not source:
        return None
    try:
        return uuid.UUID(str(source.get("document_id")))
    except (TypeError, ValueError):
        return None


def _ensure_disposition(disposition: str) -> None:
    if disposition not in {item.value for item in QaDisposition}:
        raise BadRequestError("处置结论不合法")
