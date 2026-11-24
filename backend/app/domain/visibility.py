"""密级可见范围：检索与问答治理共用的唯一判定来源。

规则本身很简单（管理员看全部，其他人到配置的可见上限），但一旦两处各写一份，
治理端就会出现"检索看不到的片段，在队列里能看到"的旁路。
"""

from __future__ import annotations

from app.core.config import settings
from app.core.deps import Principal
from app.domain.enums import RoleCode, Sensitivity

SENSITIVITY_ORDER: list[str] = [
    Sensitivity.PUBLIC.value,
    Sensitivity.INTERNAL.value,
    Sensitivity.CONFIDENTIAL.value,
    Sensitivity.SECRET.value,
]


def allowed_sensitivities(principal: Principal) -> list[str]:
    """管理员看全部；其他人只到配置的可见上限（默认 public + internal）。"""
    if principal.has_role(RoleCode.ADMIN.value):
        return list(SENSITIVITY_ORDER)
    limit = settings.retrieval_max_sensitivity
    if limit not in SENSITIVITY_ORDER:
        limit = Sensitivity.INTERNAL.value
    return SENSITIVITY_ORDER[: SENSITIVITY_ORDER.index(limit) + 1]


def highest_sensitivity(values: list[str]) -> str:
    """取一组密级中的最高级；空集合视为 ``public``（无内容可泄露）。"""
    rank = -1
    result = Sensitivity.PUBLIC.value
    for value in values:
        if value in SENSITIVITY_ORDER and SENSITIVITY_ORDER.index(value) > rank:
            rank = SENSITIVITY_ORDER.index(value)
            result = value
    return result


__all__ = ["SENSITIVITY_ORDER", "allowed_sensitivities", "highest_sensitivity"]
