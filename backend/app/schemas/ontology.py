"""本体的请求/响应模型。

属性定义（``attributes``）在这里从"自由 JSON"升级为契约：
键是属性编码，值是 :class:`AttributeDef`。存量数据可能只有
``{"type": "...", "description": "..."}``，读取时由 ``_upgrade_one`` 补齐默认值，
不要求数据库迁移；保存时则整体写回新结构。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

MAX_ATTRIBUTES = 30
MAX_EXAMPLES = 3
# 大小写均可：首字符必须是字母，其后为字母、数字或下划线，总长 2-64。
_CODE_PATTERN = r"^[A-Za-z][A-Za-z0-9_]{1,63}$"

AttributeType = Literal["string", "number", "date", "boolean", "enum", "array"]
SCALAR_TYPES: tuple[str, ...] = ("string", "number", "date", "boolean")


class AttributeDef(BaseModel):
    """一个属性（或关系属性）的定义。"""

    code: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=64)
    type: AttributeType
    description: str = Field(min_length=1, max_length=1000)
    hint: str | None = Field(default=None, max_length=500)
    examples: list[str] = Field(default_factory=list)
    required: bool = False
    enum_values: list[str] | None = None
    items_type: AttributeType | None = None

    @field_validator("examples")
    @classmethod
    def _limit_examples(cls, value: list[str]) -> list[str]:
        if len(value) > MAX_EXAMPLES:
            raise ValueError(f"属性示例不得超过 {MAX_EXAMPLES} 条")
        cleaned = [item.strip() for item in value if item and item.strip()]
        for item in cleaned:
            if len(item) > 200:
                raise ValueError("单条属性示例不得超过 200 字符")
        return cleaned

    @model_validator(mode="after")
    def _check_type_fields(self) -> AttributeDef:
        if self.type == "enum":
            values = [item.strip() for item in (self.enum_values or []) if item.strip()]
            if not values:
                raise ValueError("枚举类型必须声明 enum_values 取值")
            if self.items_type is not None:
                raise ValueError("枚举类型不得声明 items_type")
            self.enum_values = list(dict.fromkeys(values))
        elif self.type == "array":
            if self.items_type is None:
                raise ValueError("数组类型必须声明 items_type 元素类型")
            if self.items_type == "array":
                raise ValueError("数组元素类型不能再是数组")
            if self.enum_values is not None:
                raise ValueError("数组类型不得声明 enum_values")
        else:
            if self.enum_values is not None:
                raise ValueError("仅枚举类型可以声明 enum_values")
            if self.items_type is not None:
                raise ValueError("仅数组类型可以声明 items_type")
        if not self.code or not self.code[0].isalpha():
            raise ValueError("属性编码需以字母开头")
        return self


def _upgrade_one(code: str, raw: Any, *, lenient: bool = False) -> dict[str, Any]:
    """把任意历史形态的属性定义补齐成新结构。

    ``lenient`` 只用于读取存量行：历史上没有校验，`array` 可能没写元素类型、
    `enum` 可能没有取值。这类定义无法直接满足新契约，读取时退化成文本属性，
    否则一条老数据会让整个列表接口 500。写入路径保持严格，仍由校验返回 400。
    """
    if isinstance(raw, AttributeDef):
        return raw.model_dump()
    if isinstance(raw, str):
        raw = {"type": "string", "description": raw}
    if not isinstance(raw, dict):
        raise ValueError(f"属性 {code} 的定义必须是对象")

    payload: dict[str, Any] = dict(raw)
    payload["code"] = str(payload.get("code") or code)
    payload["name"] = str(payload.get("name") or payload["code"])
    payload["type"] = payload.get("type") or "string"
    description = payload.get("description")
    payload["description"] = str(description).strip() if description else payload["name"]
    payload.setdefault("examples", [])
    payload.setdefault("required", False)
    payload.setdefault("hint", None)
    payload.setdefault("enum_values", None)
    payload.setdefault("items_type", None)

    if lenient:
        if payload["type"] == "array" and payload["items_type"] in (None, "", "array"):
            payload["items_type"] = "string"
        elif payload["type"] == "enum" and not payload["enum_values"]:
            payload["type"] = "string"
        if payload["type"] != "array":
            payload["items_type"] = None
        if payload["type"] != "enum":
            payload["enum_values"] = None
    return payload


def normalize_attributes(value: Any, *, lenient: bool = False) -> dict[str, Any]:
    """字典形态的升级入口，服务层复用它把 ORM 行转成新结构。"""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("属性定义必须是对象")
    return {
        str(code): _upgrade_one(str(code), raw, lenient=lenient)
        for code, raw in value.items()
    }


def _dump_attributes(attributes: dict[str, AttributeDef]) -> dict[str, Any]:
    return {
        code: attribute.model_dump(exclude_none=False) for code, attribute in attributes.items()
    }


class _AttributeMixin(BaseModel):
    attributes: dict[str, AttributeDef] = Field(default_factory=dict)

    @field_validator("attributes", mode="before")
    @classmethod
    def _upgrade(cls, value: Any) -> Any:
        return normalize_attributes(value)

    @model_validator(mode="after")
    def _check_map(self) -> Any:
        if len(self.attributes) > MAX_ATTRIBUTES:
            raise ValueError(f"属性定义不得超过 {MAX_ATTRIBUTES} 个")
        for code, attribute in self.attributes.items():
            if attribute.code != code:
                raise ValueError(f"属性 {code} 的 code 字段与键不一致")
        return self

    def attributes_payload(self) -> dict[str, Any]:
        return _dump_attributes(self.attributes)


class OntologyClassCreateRequest(_AttributeMixin):
    code: str = Field(pattern=_CODE_PATTERN)
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    parent_id: UUID | None = None
    identity_keys: list[str] = Field(default_factory=list)
    status: Literal[0, 1]

    @field_validator("identity_keys")
    @classmethod
    def _check_identity_keys(cls, value: list[str]) -> list[str]:
        return _validate_identity_keys(value)


class OntologyClassUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    parent_id: UUID | None = None
    attributes: dict[str, AttributeDef] | None = None
    identity_keys: list[str] | None = None
    status: Literal[0, 1] | None = None

    @field_validator("attributes", mode="before")
    @classmethod
    def _upgrade_optional(cls, value: Any) -> Any:
        if value is None:
            return None
        return normalize_attributes(value)

    @model_validator(mode="after")
    def _check_optional_map(self) -> Any:
        if self.attributes is None:
            return self
        if len(self.attributes) > MAX_ATTRIBUTES:
            raise ValueError(f"属性定义不得超过 {MAX_ATTRIBUTES} 个")
        for code, attribute in self.attributes.items():
            if attribute.code != code:
                raise ValueError(f"属性 {code} 的 code 字段与键不一致")
        return self

    @field_validator("identity_keys")
    @classmethod
    def _check_identity_keys(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return _validate_identity_keys(value)

    def attributes_payload(self) -> dict[str, Any] | None:
        if self.attributes is None:
            return None
        return _dump_attributes(self.attributes)


def _validate_identity_keys(value: list[str] | None) -> list[str]:
    if not value:
        return []
    cleaned: list[str] = []
    for item in value:
        code = str(item).strip()
        if not code:
            continue
        if code in cleaned:
            raise ValueError(f"标识键 {code} 重复")
        cleaned.append(code)
    return cleaned


class OntologyClassItem(BaseModel):
    id: str
    code: str
    name: str
    description: str | None = None
    parent_id: str | None = None
    attributes: dict[str, AttributeDef] = Field(default_factory=dict)
    identity_keys: list[str] = Field(default_factory=list)
    version: int
    status: int
    created_at: datetime
    updated_at: datetime

    @field_validator("attributes", mode="before")
    @classmethod
    def _upgrade(cls, value: Any) -> Any:
        return normalize_attributes(value, lenient=True)


class OntologyRelationCreateRequest(_AttributeMixin):
    code: str = Field(pattern=_CODE_PATTERN)
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    source_class_id: UUID
    target_class_id: UUID
    status: Literal[0, 1]


class OntologyRelationUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    source_class_id: UUID | None = None
    target_class_id: UUID | None = None
    attributes: dict[str, AttributeDef] | None = None
    status: Literal[0, 1] | None = None

    @field_validator("attributes", mode="before")
    @classmethod
    def _upgrade_optional(cls, value: Any) -> Any:
        if value is None:
            return None
        return normalize_attributes(value)

    @model_validator(mode="after")
    def _check_optional_map(self) -> Any:
        if self.attributes is None:
            return self
        if len(self.attributes) > MAX_ATTRIBUTES:
            raise ValueError(f"属性定义不得超过 {MAX_ATTRIBUTES} 个")
        for code, attribute in self.attributes.items():
            if attribute.code != code:
                raise ValueError(f"属性 {code} 的 code 字段与键不一致")
        return self

    def attributes_payload(self) -> dict[str, Any] | None:
        if self.attributes is None:
            return None
        return _dump_attributes(self.attributes)


class OntologyRelationItem(BaseModel):
    id: str
    code: str
    name: str
    description: str | None = None
    source_class_id: str
    target_class_id: str
    source_class_code: str
    target_class_code: str
    attributes: dict[str, AttributeDef] = Field(default_factory=dict)
    version: int
    status: int
    created_at: datetime
    updated_at: datetime

    @field_validator("attributes", mode="before")
    @classmethod
    def _upgrade(cls, value: Any) -> Any:
        return normalize_attributes(value, lenient=True)


class OntologyClassListResponse(BaseModel):
    items: list[OntologyClassItem]
    total: int
    version: int


class OntologyRelationListResponse(BaseModel):
    items: list[OntologyRelationItem]
    total: int
    version: int
