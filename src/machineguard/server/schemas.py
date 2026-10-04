"""严格身份请求，不把密码或未知字段回显到错误响应。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PredictionRequest(StrictRequest):
    engine_key: str = Field(min_length=1, max_length=96)
    end_cycle: int = Field(ge=1, le=2147483647)
    model_version: str | None = Field(default=None, min_length=1, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")


class LoginRequest(StrictRequest):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-z0-9][a-z0-9_.-]{2,63}$")
    password: SecretStr

    @field_validator("username", mode="before")
    @classmethod
    def normalized_username(cls, value):
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("password")
    @classmethod
    def bounded_password(cls, value: SecretStr) -> SecretStr:
        if not 1 <= len(value.get_secret_value()) <= 128:
            raise ValueError("密码长度不符合要求。")
        return value


class CreateUserRequest(LoginRequest):
    display_name: str = Field(min_length=1, max_length=128)
    roles: list[Literal["admin", "analyst", "reviewer"]] = Field(min_length=1, max_length=3)

    @field_validator("password")
    @classmethod
    def strong_password_length(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 12:
            raise ValueError("新账号密码至少需要 12 个字符。")
        return value

    @field_validator("roles")
    @classmethod
    def unique_roles(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("角色不能重复。")
        return values


class UpdateUserRequest(StrictRequest):
    is_active: bool
    roles: list[Literal["admin", "analyst", "reviewer"]] = Field(min_length=1, max_length=3)

    @field_validator("roles")
    @classmethod
    def unique_roles(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("角色不能重复。")
        return values
