"""身份业务统一执行授权，事务由调用入口提交。"""

from dataclasses import dataclass
from datetime import timedelta
import re
import secrets
from uuid import UUID, uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from .models import AuditEvent, Role, SessionRecord, User, UserRole
from .schemas import CreateUserRequest, UpdateUserRequest
from .security import DUMMY_HASH, PASSWORD_HASHER, permissions, token_digest, verify_password
from .settings import ServerSettings


class ServiceError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)


@dataclass
class Principal:
    user: User
    session: SessionRecord

    def require(self, permission: str) -> None:
        if permission not in permissions([role.name for role in self.user.roles]):
            raise ServiceError(403, "PERMISSION_DENIED", "当前身份没有此操作权限。")


def user_view(user: User) -> dict:
    names = sorted(role.name for role in user.roles)
    return {"id": str(user.id), "username": user.username, "display_name": user.display_name,
            "roles": names, "permissions": permissions(names), "is_guest": user.is_guest,
            "is_active": user.is_active, "created_at": user.created_at.isoformat()}


def audit(session: Session, actor_id: UUID | None, action: str, target_type: str,
          target_id: UUID | None, request_id: UUID | None, details: dict) -> None:
    """显式构造审计详情，调用者不能传入密码或令牌。"""
    if any(key.lower() in {"password", "token", "access_token", "password_hash", "token_hash"} for key in details):
        raise ValueError("审计不能记录密码、令牌或其哈希。")
    session.add(AuditEvent(actor_id=actor_id, action=action, target_type=target_type,
                           target_id=target_id, request_id=request_id, details=details))


def _issue(session: Session, user: User, lifetime: timedelta, request_id: UUID) -> dict:
    token = secrets.token_urlsafe(32)
    now = session.scalar(select(func.now()))
    record = SessionRecord(user_id=user.id, token_hash=token_digest(token), expires_at=now + lifetime)
    session.add(record)
    session.flush()
    audit(session, user.id, "auth.session_created", "session", record.id, request_id, {"guest": user.is_guest})
    return {"access_token": token, "token_type": "Bearer", "expires_at": record.expires_at.isoformat(),
            "session_id": str(record.id), "user": user_view(user)}


def login(session: Session, username: str, password: str, settings: ServerSettings, request_id: UUID) -> dict:
    user = session.scalar(select(User).where(User.username == username))
    saved_hash = user.password_hash if user is not None and user.password_hash is not None else DUMMY_HASH
    verified = verify_password(password, saved_hash)
    if user is None or not verified or not user.is_active or user.is_guest:
        raise ServiceError(401, "LOGIN_FAILED", "账号或密码不正确，或账号不可用。")
    if PASSWORD_HASHER.check_needs_rehash(user.password_hash):
        user.password_hash = PASSWORD_HASHER.hash(password)
    return _issue(session, user, timedelta(hours=settings.session_hours), request_id)


def guest_session(session: Session, settings: ServerSettings, request_id: UUID) -> dict:
    user = User(username="guest-" + uuid4().hex, display_name="访客", is_guest=True,
                password_hash=None, roles=[session.get(Role, "guest")])
    session.add(user)
    session.flush()
    return _issue(session, user, timedelta(minutes=settings.guest_minutes), request_id)


def authenticate(session: Session, token: str) -> Principal:
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise ServiceError(401, "SESSION_INVALID", "会话无效、已过期或已撤销。")
    record = session.scalar(select(SessionRecord).where(SessionRecord.token_hash == token_digest(token)))
    now = session.scalar(select(func.now()))
    if record is None or record.revoked_at is not None or record.expires_at <= now or not record.user.is_active:
        raise ServiceError(401, "SESSION_INVALID", "会话无效、已过期或已撤销。")
    return Principal(record.user, record)


def logout(session: Session, principal: Principal, request_id: UUID) -> None:
    principal.session.revoked_at = session.scalar(select(func.now()))
    audit(session, principal.user.id, "auth.session_revoked", "session", principal.session.id, request_id, {})


def create_user(session: Session, principal: Principal, payload: CreateUserRequest, request_id: UUID) -> User:
    principal.require("users:manage")
    if session.scalar(select(User.id).where(User.username == payload.username)) is not None:
        raise ServiceError(409, "USERNAME_EXISTS", "账号名称已存在。")
    user = User(username=payload.username, display_name=payload.display_name,
                password_hash=PASSWORD_HASHER.hash(payload.password.get_secret_value()),
                roles=[session.get(Role, name) for name in payload.roles])
    session.add(user)
    session.flush()
    audit(session, principal.user.id, "user.created", "user", user.id, request_id,
          {"username": user.username, "roles": sorted(payload.roles)})
    return user


def update_user(session: Session, principal: Principal, user_id: UUID, payload: UpdateUserRequest, request_id: UUID) -> User:
    principal.require("users:manage")
    # 串行化管理员变更，防止并发请求同时停用最后两个管理员。
    session.execute(text("SELECT pg_advisory_xact_lock(774413001)"))
    user = session.get(User, user_id)
    if user is None:
        raise ServiceError(404, "USER_NOT_FOUND", "账号不存在。")
    if user.is_guest:
        raise ServiceError(409, "GUEST_ROLE_FIXED", "访客账号不能变更为正式账号。")
    was_admin = user.is_active and any(role.name == "admin" for role in user.roles)
    remains_admin = payload.is_active and "admin" in payload.roles
    if was_admin and not remains_admin:
        count = session.scalar(select(func.count()).select_from(User).join(UserRole).where(
            User.is_active.is_(True), UserRole.role_name == "admin"))
        if count <= 1:
            raise ServiceError(409, "LAST_ADMIN_REQUIRED", "至少保留一个启用的管理员。")
    user.is_active = payload.is_active
    user.roles = [session.get(Role, name) for name in payload.roles]
    # 停用或变更权限立即撤销该用户全部会话，下一次请求必须重新登录。
    now = session.scalar(select(func.now()))
    session.execute(update(SessionRecord).where(SessionRecord.user_id == user.id, SessionRecord.revoked_at.is_(None)).values(revoked_at=now))
    audit(session, principal.user.id, "user.updated", "user", user.id, request_id,
          {"is_active": payload.is_active, "roles": sorted(payload.roles)})
    session.flush()
    return user
