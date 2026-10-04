"""Argon2id 密码和随机会话令牌，共用角色权限规则。"""

from hashlib import sha256
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError


PASSWORD_HASHER = PasswordHasher()
# 未知账号也执行密码校验，响应不暴露账号是否存在。
DUMMY_HASH = PASSWORD_HASHER.hash(secrets.token_urlsafe(32))
ROLE_PERMISSIONS = {
    "admin": {"users:manage", "devices:read", "predictions:create", "analysis:create", "reports:review", "knowledge:manage", "system:read"},
    "analyst": {"devices:read", "predictions:create", "analysis:create"},
    "reviewer": {"devices:read", "predictions:create", "reports:review"},
    "guest": {"devices:read", "predictions:create", "analysis:create"},
}


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return PASSWORD_HASHER.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def token_digest(token: str) -> str:
    return sha256(token.encode("ascii")).hexdigest()


def permissions(role_names: list[str]) -> list[str]:
    return sorted(set().union(*(ROLE_PERMISSIONS[name] for name in role_names)))
