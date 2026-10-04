"""严格来源元数据；上传原文不执行其中的指令。"""

from datetime import date
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SourceMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True, hide_input_in_errors=True)
    title: str = Field(min_length=1, max_length=200)
    author_or_organization: str = Field(min_length=1, max_length=200)
    publication_date: str | None
    equipment_type: str = Field(min_length=1, max_length=200)
    applicable_scope: str = Field(min_length=1, max_length=1000)
    source_url: str | None
    source_kind: Literal["original", "project_summary", "simulated_sop"]
    license: str = Field(min_length=1, max_length=200)
    usage_boundary: str = Field(min_length=1, max_length=1000)
    redistribution_allowed: bool

    @field_validator("publication_date")
    @classmethod
    def valid_date(cls, value):
        if value is not None:
            date.fromisoformat(value)
            if len(value) != 10:
                raise ValueError("日期必须为 YYYY-MM-DD。")
        return value

    @field_validator("source_url")
    @classmethod
    def valid_url(cls, value):
        if value is not None:
            parsed = urlparse(value)
            if len(value) > 2000 or parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password or any(c.isspace() for c in value):
                raise ValueError("来源链接必须为无凭据 HTTP(S) 地址。")
        return value

    @model_validator(mode="after")
    def valid_boundary(self):
        if self.redistribution_allowed and self.license.lower() in {"unknown", "未知", "不明", "待确认"}:
            raise ValueError("未知许可不能标记允许公开再分发。")
        for value in self.model_dump().values():
            if isinstance(value, str) and any(ord(c) < 32 and c not in "\n\r\t" for c in value):
                raise ValueError("元数据含非法控制字符。")
        return self
