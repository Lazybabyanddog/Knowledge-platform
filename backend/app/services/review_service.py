"""文档审批服务：配置审批流、提交、批准/驳回/撤回与待办查询。

审批的关键约束都落在这一层：
- 提交时冻结版本并把流程快照进实例，之后改流程不影响在途审批；
- 只有当前步骤的审批人能动作，且同一人同一步骤只能批准一次；
- 最后一步批准后由服务层切换 ``is_current``，保证任一时刻只有一个生效版本。
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.exceptions import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.domain import state_machines
from app.domain.enums import (
    AuditAction,
    DocumentStatus,
    ReviewAction,
    ReviewInstanceStatus,
    RoleCode,
)
from app.infra.db.models import Document, DocumentVersion, ReviewFlow, ReviewInstance
from app.infra.db.models.review import ReviewRecord
from app.infra.db.repositories import DocumentRepository, ReviewRepository, UserRepository
from app.schemas.review import (
    ReviewFlowCreateRequest,
    ReviewFlowItem,
    ReviewFlowListResponse,
    ReviewFlowUpdateRequest,
    ReviewInstanceDetail,
    ReviewInstanceItem,
    ReviewListResponse,
    ReviewQuery,
    ReviewRecordItem,
    ReviewRejectRequest,
    ReviewStepItem,
    ReviewSubmitRequest,
)
from app.services.audit_service import AuditService
from app.services.index_service import enqueue_version_index, reset_version_index

OVERDUE_WORKDAYS = 3
ROLE_CODES = {role.value for role in RoleCode}


def business_days_between(start: datetime, end: datetime) -> int:
    """按工作日（周一至周五）计算等待天数，不含法定节假日。

    以"跨过多少个工作日"计：周五提交、周一查看算 1 个工作日。
    """
    if end <= start:
        return 0
    days = 0
    cursor: date = start.date()
    while cursor < end.date():
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            days += 1
    return days


def normalize_steps(steps: list[dict[str, Any]] | list[ReviewStepItem]) -> list[dict[str, Any]]:
    """统一补全 step_no，保证 DB 里的历史数据也能安全读取。"""
    normalized: list[dict[str, Any]] = []
    for index, step in enumerate(steps, start=1):
        data = step.model_dump() if isinstance(step, ReviewStepItem) else dict(step)
        normalized.append(
            {
                "step_no": index,
                "name": data.get("name", f"第 {index} 步"),
                "approver_type": data.get("approver_type", "role"),
                "approver_value": data.get("approver_value", ""),
                "skippable": bool(data.get("skippable", False)),
            }
        )
    return normalized


def _step_at(instance: ReviewInstance, step_no: int | None = None) -> dict[str, Any] | None:
    steps = normalize_steps(instance.flow_snapshot or [])
    target = step_no if step_no is not None else instance.current_step
    for step in steps:
        if step["step_no"] == target:
            return step
    return None


def _is_approver(step: dict[str, Any] | None, principal: Principal) -> bool:
    if not step:
        return False
    if step.get("approver_type") == "role":
        return principal.has_role(str(step.get("approver_value")))
    return str(step.get("approver_value")) == str(principal.user_id)


def flow_item(flow: ReviewFlow) -> ReviewFlowItem:
    steps = [ReviewStepItem(**step) for step in normalize_steps(flow.steps or [])]
    return ReviewFlowItem(
        id=str(flow.id),
        name=flow.name,
        description=flow.description,
        doc_type=flow.doc_type,
        steps=steps,
        is_default=flow.is_default,
        status=flow.status,
        created_at=flow.created_at,
        updated_at=flow.updated_at,
    )


def record_item(record: ReviewRecord) -> ReviewRecordItem:
    return ReviewRecordItem(
        id=str(record.id),
        step_no=record.step_no,
        step_name=record.step_name,
        action=record.action,  # type: ignore[arg-type]
        actor_id=str(record.actor_id) if record.actor_id else None,
        actor_name=record.actor_name,
        comment=record.comment,
        created_at=record.created_at,
    )


def instance_item(
    instance: ReviewInstance,
    *,
    document_title: str,
    doc_type: str,
    version_no: int,
    submitter_name: str | None,
    last_action_at: datetime | None,
    now: datetime | None = None,
) -> ReviewInstanceItem:
    now = now or datetime.now(UTC)
    steps = normalize_steps(instance.flow_snapshot or [])
    current = _step_at(instance, instance.current_step)
    step_started_at = last_action_at or instance.submitted_at
    waiting = (
        business_days_between(step_started_at, now)
        if instance.status == ReviewInstanceStatus.PENDING.value
        else 0
    )
    return ReviewInstanceItem(
        id=str(instance.id),
        document_id=str(instance.document_id),
        document_title=document_title,
        doc_type=doc_type,
        version_no=version_no,
        flow_id=str(instance.flow_id) if instance.flow_id else None,
        flow_name=instance.flow_name,
        status=instance.status,  # type: ignore[arg-type]
        current_step=instance.current_step,
        total_steps=len(steps),
        current_step_name=current.get("name") if current else None,
        submitted_by=str(instance.submitted_by) if instance.submitted_by else None,
        submitter_name=submitter_name,
        submitted_at=instance.submitted_at,
        finished_at=instance.finished_at,
        step_started_at=step_started_at,
        waiting_workdays=waiting,
        overdue=(
            instance.status == ReviewInstanceStatus.PENDING.value
            and waiting > OVERDUE_WORKDAYS
        ),
    )


class ReviewService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.reviews = ReviewRepository(db)
        self.documents = DocumentRepository(db)
        self.users = UserRepository(db)
        self.audit = AuditService(db)

    # ------------------------------------------------------------------ #
    # 审批流配置
    # ------------------------------------------------------------------ #
    def create_flow(
        self, principal: Principal, payload: ReviewFlowCreateRequest
    ) -> ReviewFlowItem:
        steps = self._validate_steps(principal, payload.steps)
        if payload.is_default:
            self.reviews.clear_default(principal.user.workspace_id, payload.doc_type)

        flow = ReviewFlow(
            id=uuid.uuid4(),
            workspace_id=principal.user.workspace_id,
            name=payload.name,
            description=payload.description,
            doc_type=payload.doc_type,
            steps=steps,
            is_default=payload.is_default,
            status="active",
            created_by=principal.user_id,
        )
        self.reviews.add_flow(flow)
        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return flow_item(flow)

    def update_flow(
        self,
        principal: Principal,
        flow_id: uuid.UUID,
        payload: ReviewFlowUpdateRequest,
    ) -> ReviewFlowItem:
        flow = self.reviews.get_flow(flow_id, workspace_id=principal.user.workspace_id)
        if flow is None:
            raise NotFoundError("审批流不存在")

        if payload.steps is not None:
            flow.steps = self._validate_steps(principal, payload.steps)
        if payload.name is not None:
            flow.name = payload.name
        if payload.description is not None:
            flow.description = payload.description
        if payload.status is not None:
            flow.status = payload.status
            # 停用的流程不能再作为默认流，否则提交时会被默认流选中
            if payload.status == "disabled":
                flow.is_default = False
        if payload.is_default is not None:
            if payload.is_default:
                self.reviews.clear_default(
                    principal.user.workspace_id, flow.doc_type, exclude_id=flow.id
                )
                if flow.status != "active":
                    raise BadRequestError("停用的审批流不能设为默认")
                flow.is_default = True
            else:
                flow.is_default = False

        self.db.commit()
        return flow_item(flow)

    def list_flows(
        self,
        principal: Principal,
        *,
        doc_type: str | None = None,
        include_disabled: bool = False,
    ) -> ReviewFlowListResponse:
        flows = self.reviews.list_flows(
            principal.user.workspace_id,
            doc_type=doc_type,
            include_disabled=include_disabled,
        )
        items = [flow_item(flow) for flow in flows]
        return ReviewFlowListResponse(items=items, total=len(items))

    def get_flow(self, principal: Principal, flow_id: uuid.UUID) -> ReviewFlowItem:
        flow = self.reviews.get_flow(flow_id, workspace_id=principal.user.workspace_id)
        if flow is None:
            raise NotFoundError("审批流不存在")
        return flow_item(flow)

    def delete_flow(
        self,
        principal: Principal,
        flow_id: uuid.UUID,
        *,
        ip: str | None = None,
    ) -> None:
        """物理删除审批流。

        审批实例保存了 flow_snapshot 与 flow_name 快照，删除流程后历史实例仍可回看；
        实例上的 flow_id 依赖外键 SET NULL 置空，不会影响进行中的审批。
        """
        flow = self.reviews.get_flow(flow_id, workspace_id=principal.user.workspace_id)
        if flow is None:
            raise NotFoundError("审批流不存在")

        snapshot = {
            "name": flow.name,
            "doc_type": flow.doc_type,
            "is_default": flow.is_default,
            "status": flow.status,
            "steps": flow.steps,
        }
        self.audit.record(
            AuditAction.REVIEW_FLOW_DELETE,
            actor_id=principal.user_id,
            actor_name=principal.user.username,
            workspace_id=flow.workspace_id,
            object_type="review_flow",
            object_id=flow.id,
            before=snapshot,
            ip=ip,
        )
        self.reviews.delete_flow(flow)
        self.db.commit()

    # ------------------------------------------------------------------ #
    # 提交审批
    # ------------------------------------------------------------------ #
    def submit(
        self,
        principal: Principal,
        payload: ReviewSubmitRequest,
        *,
        ip: str | None = None,
    ) -> ReviewInstanceItem:
        document = self.documents.get(
            self._uuid_or_400(payload.document_id, "document_id"),
            workspace_id=principal.user.workspace_id,
        )
        if document is None:
            raise NotFoundError("文档不存在")
        if document.status != DocumentStatus.DRAFT.value:
            existing = self.reviews.active_instance(document.id)
            if existing is not None:
                raise ConflictError(
                    "文档已存在进行中的审批",
                    data={"review_instance_id": str(existing.id), "status": existing.status},
                )
            raise ConflictError(
                f"文档当前状态为 {document.status}，只有草稿可以提交审批",
                data={"current": document.status, "target": DocumentStatus.PENDING.value},
            )

        existing = self.reviews.active_instance(document.id)
        if existing is not None:
            raise ConflictError(
                "文档已存在进行中的审批",
                data={"review_instance_id": str(existing.id), "status": existing.status},
            )

        version = self._pick_version(document, payload.document_version_id)
        flow = self._resolve_flow(principal, document, payload.flow_id)
        snapshot = normalize_steps(flow.steps or [])
        if not snapshot:
            raise BadRequestError("审批流没有配置任何步骤")

        state_machines.DOCUMENT.ensure(document.status, DocumentStatus.PENDING.value)
        instance = ReviewInstance(
            id=uuid.uuid4(),
            workspace_id=principal.user.workspace_id,
            document_id=document.id,
            document_version_id=version.id,
            flow_id=flow.id,
            flow_name=flow.name,
            flow_snapshot=snapshot,
            status=ReviewInstanceStatus.PENDING.value,
            current_step=1,
            submitted_by=principal.user_id,
            submitted_at=datetime.now(UTC),
        )
        self.reviews.add_instance(instance)
        document.status = DocumentStatus.PENDING.value

        self.audit.record(
            AuditAction.REVIEW_SUBMIT,
            actor=principal.user,
            object_type="review_instance",
            object_id=instance.id,
            after={
                "document_id": str(document.id),
                "version_no": version.version_no,
                "flow_id": str(flow.id),
                "flow_name": flow.name,
                "total_steps": len(snapshot),
            },
            ip=ip,
        )
        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return self._item(instance, document, version)

    # ------------------------------------------------------------------ #
    # 审批动作
    # ------------------------------------------------------------------ #
    def approve(
        self,
        principal: Principal,
        instance_id: uuid.UUID,
        comment: str | None,
        *,
        ip: str | None = None,
    ) -> ReviewInstanceItem:
        instance = self._get_pending_locked(principal, instance_id)
        step = _step_at(instance)
        step_no = instance.current_step

        # 先判重复再判越权：同一人二次批准应返回 409 而不是 403
        if self.reviews.has_action(
            instance.id,
            step_no=step_no,
            actor_id=principal.user_id,
            action=ReviewAction.APPROVE.value,
        ):
            raise ConflictError("您已审批过该步骤，请勿重复操作")
        if not _is_approver(step, principal):
            raise ForbiddenError("仅当前步骤的审批人可以审批")

        document, version = self._document_and_version(instance)
        self.reviews.add_record(
            ReviewRecord(
                id=uuid.uuid4(),
                instance_id=instance.id,
                step_no=step_no,
                step_name=step.get("name") if step else None,
                action=ReviewAction.APPROVE.value,
                actor_id=principal.user_id,
                actor_name=principal.user.display_name,
                comment=comment,
            )
        )

        total_steps = len(normalize_steps(instance.flow_snapshot or []))
        if step_no >= total_steps:
            state_machines.REVIEW_INSTANCE.ensure(
                instance.status, ReviewInstanceStatus.APPROVED.value
            )
            instance.status = ReviewInstanceStatus.APPROVED.value
            instance.finished_at = datetime.now(UTC)
            self._publish(document, version)
            detail = {"published": True, "version_no": version.version_no}
        else:
            instance.current_step = step_no + 1
            detail = {"published": False, "next_step": instance.current_step}

        self.audit.record(
            AuditAction.REVIEW_APPROVE,
            actor=principal.user,
            object_type="review_instance",
            object_id=instance.id,
            after={
                "document_id": str(document.id),
                "step_no": step_no,
                "comment": comment,
                **detail,
            },
            ip=ip,
        )
        self.db.commit()
        if detail.get("published"):
            # 提交后再投递：worker 读不到未提交的版本状态
            enqueue_version_index(version.id)
        return self._item(instance, document, version)

    def reject(
        self,
        principal: Principal,
        instance_id: uuid.UUID,
        payload: ReviewRejectRequest,
        *,
        ip: str | None = None,
    ) -> ReviewInstanceItem:
        instance = self._get_pending_locked(principal, instance_id)
        step = _step_at(instance)
        step_no = instance.current_step
        if not _is_approver(step, principal):
            raise ForbiddenError("仅当前步骤的审批人可以审批")

        document, version = self._document_and_version(instance)
        self.reviews.add_record(
            ReviewRecord(
                id=uuid.uuid4(),
                instance_id=instance.id,
                step_no=step_no,
                step_name=step.get("name") if step else None,
                action=ReviewAction.REJECT.value,
                actor_id=principal.user_id,
                actor_name=principal.user.display_name,
                comment=payload.comment,
            )
        )

        state_machines.REVIEW_INSTANCE.ensure(
            instance.status, ReviewInstanceStatus.REJECTED.value
        )
        state_machines.DOCUMENT.ensure(document.status, DocumentStatus.REJECTED.value)
        instance.status = ReviewInstanceStatus.REJECTED.value
        instance.finished_at = datetime.now(UTC)
        document.status = DocumentStatus.REJECTED.value

        self.audit.record(
            AuditAction.REVIEW_REJECT,
            actor=principal.user,
            object_type="review_instance",
            object_id=instance.id,
            before={"document_status": DocumentStatus.PENDING.value},
            after={
                "document_id": str(document.id),
                "document_status": document.status,
                "step_no": step_no,
                "comment": payload.comment,
            },
            ip=ip,
        )
        self.db.commit()
        return self._item(instance, document, version)

    def withdraw(
        self,
        principal: Principal,
        instance_id: uuid.UUID,
        comment: str | None,
        *,
        ip: str | None = None,
    ) -> ReviewInstanceItem:
        instance = self._get_pending_locked(principal, instance_id, lock=False)
        if instance.submitted_by != principal.user_id:
            raise ForbiddenError("只有提交人可以撤回审批")

        document, version = self._document_and_version(instance)
        step = _step_at(instance)
        self.reviews.add_record(
            ReviewRecord(
                id=uuid.uuid4(),
                instance_id=instance.id,
                step_no=instance.current_step,
                step_name=step.get("name") if step else None,
                action=ReviewAction.WITHDRAW.value,
                actor_id=principal.user_id,
                actor_name=principal.user.display_name,
                comment=comment,
            )
        )

        state_machines.REVIEW_INSTANCE.ensure(
            instance.status, ReviewInstanceStatus.WITHDRAWN.value
        )
        state_machines.DOCUMENT.ensure(document.status, DocumentStatus.DRAFT.value)
        instance.status = ReviewInstanceStatus.WITHDRAWN.value
        instance.finished_at = datetime.now(UTC)
        document.status = DocumentStatus.DRAFT.value

        self.audit.record(
            AuditAction.REVIEW_WITHDRAW,
            actor=principal.user,
            object_type="review_instance",
            object_id=instance.id,
            before={"document_status": DocumentStatus.PENDING.value},
            after={
                "document_id": str(document.id),
                "document_status": document.status,
                "step_no": instance.current_step,
                "comment": comment,
            },
            ip=ip,
        )
        self.db.commit()
        return self._item(instance, document, version)

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    def list_instances(
        self, principal: Principal, query: ReviewQuery
    ) -> ReviewListResponse:
        mine = query.scope == "mine"
        if not mine and not principal.has_role(RoleCode.ADMIN.value):
            raise ForbiddenError("仅管理员可以查看全量审批")

        rows, total = self.reviews.list_instances(
            workspace_id=principal.user.workspace_id,
            status=ReviewInstanceStatus.PENDING.value if mine else query.status,
            doc_type=query.doc_type,
            submitted_by=self._optional_uuid(query.submitted_by, "submitted_by"),
            document_id=self._optional_uuid(query.document_id, "document_id"),
            created_from=query.created_from,
            created_to=query.created_to,
            approver_roles=principal.roles if mine else None,
            approver_user_id=principal.user_id if mine else None,
            page=query.page,
            page_size=query.page_size,
            newest_first=not mine,
        )
        last_times = self.reviews.latest_record_times([row[0].id for row in rows])
        now = datetime.now(UTC)
        items = [
            instance_item(
                instance,
                document_title=title,
                doc_type=doc_type,
                version_no=version_no,
                submitter_name=submitter_name,
                last_action_at=last_times.get(instance.id),
                now=now,
            )
            for instance, title, doc_type, version_no, submitter_name in rows
        ]
        return ReviewListResponse(
            items=items, total=total, page=query.page, page_size=query.page_size
        )

    def get_detail(
        self, principal: Principal, instance_id: uuid.UUID
    ) -> ReviewInstanceDetail:
        instance = self.reviews.get_instance(
            instance_id, workspace_id=principal.user.workspace_id
        )
        if instance is None:
            raise NotFoundError("审批实例不存在")
        document, version = self._document_and_version(instance)
        last_times = self.reviews.latest_record_times([instance.id])
        base = self._item(
            instance, document, version, last_action_at=last_times.get(instance.id)
        )
        records = self.reviews.list_records(instance.id)
        return ReviewInstanceDetail(
            **base.model_dump(),
            steps=[
                ReviewStepItem(**step) for step in normalize_steps(instance.flow_snapshot or [])
            ],
            records=[record_item(record) for record in records],
        )

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _validate_steps(self, principal: Principal, steps: list[Any]) -> list[dict[str, Any]]:
        for index, step in enumerate(steps, start=1):
            if step.approver_type == "role":
                if step.approver_value not in ROLE_CODES:
                    raise BadRequestError(
                        f"第 {index} 步审批角色不存在: {step.approver_value}"
                    )
                continue
            user_id = self._uuid_or_400(step.approver_value, f"steps[{index}].approver_value")
            user = self.users.get(user_id)
            if user is None or user.workspace_id != principal.user.workspace_id:
                raise BadRequestError(f"第 {index} 步指定的审批人不存在")
        return normalize_steps(steps)

    def _pick_version(
        self, document: Document, version_id: str | None
    ) -> DocumentVersion:
        if version_id:
            version = self.documents.get_version(
                document.id, self._uuid_or_400(version_id, "document_version_id")
            )
            if version is None:
                raise BadRequestError("指定的版本不属于该文档")
            return version
        versions = self.documents.list_versions(document.id)
        if not versions:
            raise BadRequestError("文档还没有可提交的版本")
        return versions[0]

    def _resolve_flow(
        self, principal: Principal, document: Document, flow_id: str | None
    ) -> ReviewFlow:
        if flow_id:
            flow = self.reviews.get_flow(
                self._uuid_or_400(flow_id, "flow_id"), workspace_id=principal.user.workspace_id
            )
            if flow is None:
                raise BadRequestError("审批流不存在")
            if flow.status != "active":
                raise BadRequestError("审批流已停用")
            if flow.doc_type != document.doc_type:
                raise BadRequestError(
                    f"审批流适用类型为 {flow.doc_type}，与文档类型 {document.doc_type} 不符"
                )
            return flow

        flow = self.reviews.default_flow(principal.user.workspace_id, document.doc_type)
        if flow is None:
            raise BadRequestError(
                f"文档类型 {document.doc_type} 尚未配置默认审批流，请先由管理员配置"
            )
        return flow

    def _get_pending_locked(
        self, principal: Principal, instance_id: uuid.UUID, *, lock: bool = True
    ) -> ReviewInstance:
        instance = self.reviews.get_instance(
            instance_id,
            workspace_id=principal.user.workspace_id,
            for_update=lock,
        )
        if instance is None:
            raise NotFoundError("审批实例不存在")
        if instance.status != ReviewInstanceStatus.PENDING.value:
            raise ConflictError(
                f"审批已结束，当前状态为 {instance.status}",
                data={"status": instance.status},
            )
        return instance

    def _document_and_version(
        self, instance: ReviewInstance
    ) -> tuple[Document, DocumentVersion]:
        document = self.documents.get(instance.document_id, workspace_id=instance.workspace_id)
        if document is None:
            raise NotFoundError("审批关联的文档不存在")
        version = self.documents.get_version(document.id, instance.document_version_id)
        if version is None:
            raise ConflictError("审批关联的文档版本不存在")
        return document, version

    def _publish(self, document: Document, version: DocumentVersion) -> None:
        """发布被批准版本：旧版本降级为历史，新版本成为唯一生效版本。"""
        previous = (
            self.documents.get_version(document.id, document.current_version_id)
            if document.current_version_id
            else None
        )
        if previous is not None and previous.id != version.id:
            previous.is_current = False
        version.is_current = True
        document.current_version_id = version.id

        state_machines.DOCUMENT.ensure(document.status, DocumentStatus.PUBLISHED.value)
        document.status = DocumentStatus.PUBLISHED.value
        # 只有当前版本会被索引，旧版本的片段在这里清掉
        reset_version_index(self.db, version)

    def _item(
        self,
        instance: ReviewInstance,
        document: Document,
        version: DocumentVersion,
        *,
        last_action_at: datetime | None = None,
    ) -> ReviewInstanceItem:
        submitter = self.users.get(instance.submitted_by) if instance.submitted_by else None
        return instance_item(
            instance,
            document_title=document.title,
            doc_type=document.doc_type,
            version_no=version.version_no,
            submitter_name=submitter.display_name if submitter else None,
            last_action_at=last_action_at,
        )

    @staticmethod
    def _uuid_or_400(value: str, field_name: str) -> uuid.UUID:
        try:
            return uuid.UUID(str(value))
        except ValueError as exc:
            raise BadRequestError(f"{field_name} 不是合法的 UUID") from exc

    @staticmethod
    def _optional_uuid(value: str | None, field_name: str) -> uuid.UUID | None:
        if not value:
            return None
        return ReviewService._uuid_or_400(value, field_name)
