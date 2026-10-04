"""后端连接配置，凭据使用 SecretStr，禁止替换数据库。"""

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from urllib.parse import urlparse

from ..paths import get_project_root


class ServerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MG_", env_file=get_project_root() / ".env",
                                      env_file_encoding="utf-8", extra="forbid", hide_input_in_errors=True)
    database_url: SecretStr
    migration_database_url: SecretStr
    cors_origins: list[str]
    session_hours: int = Field(ge=1, le=24)
    guest_minutes: int = Field(ge=5, le=60)

    @field_validator("database_url", "migration_database_url")
    @classmethod
    def postgres_only(cls, value: SecretStr) -> SecretStr:
        url = make_url(value.get_secret_value())
        if url.drivername != "postgresql+psycopg" or not all((url.username, url.password, url.host, url.database)):
            raise ValueError("连接必须完整指定 postgresql+psycopg，不使用其它数据库。")
        return value

    @field_validator("cors_origins")
    @classmethod
    def exact_origins(cls, values: list[str]) -> list[str]:
        if not values or len(set(values)) != len(values):
            raise ValueError("CORS 域名清单必须非空且唯一。")
        for value in values:
            parsed = urlparse(value)
            if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username or "*" in value:
                raise ValueError("CORS 只允许完整明确的 http/https 来源。")
        return values
