"""私有 OSS 原文存储；严格配置、禁止覆盖、不替换为本地存储。"""

from hashlib import sha256
import json
import os
import re

import alibabacloud_oss_v2 as oss
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError, model_validator

from ..paths import get_project_root
from .auth_service import ServiceError


MAX_FILE_BYTES = 10 * 1024 * 1024


class OssSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    MG_OSS_REGION: str
    MG_OSS_ENDPOINT: str
    MG_OSS_BUCKET: str
    MG_OSS_PREFIX: str
    MG_OSS_ACCESS_KEY_ID: SecretStr
    MG_OSS_ACCESS_KEY_SECRET: SecretStr

    @model_validator(mode="after")
    def valid(self):
        if not re.fullmatch(r"[a-z][a-z0-9-]{2,40}", self.MG_OSS_REGION):
            raise ValueError("OSS 地域非法。")
        if self.MG_OSS_ENDPOINT != f"https://oss-{self.MG_OSS_REGION}.aliyuncs.com":
            raise ValueError("OSS 必须使用对应地域的 HTTPS 外网 Endpoint。")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", self.MG_OSS_BUCKET):
            raise ValueError("OSS Bucket 名称非法。")
        if self.MG_OSS_PREFIX != "machineguard/":
            raise ValueError("OSS 前缀必须与本轮 RAM 策略一致。")
        for value in (self.MG_OSS_ACCESS_KEY_ID, self.MG_OSS_ACCESS_KEY_SECRET):
            raw = value.get_secret_value()
            if not raw or raw.startswith("YOUR_") or any(char.isspace() for char in raw):
                raise ValueError("OSS 凭据未填写或含空白。")
        return self

    @classmethod
    def load(cls):
        path = get_project_root() / "configs" / "oss.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
            if set(raw) != {"region", "endpoint", "bucket", "prefix", "access_key_id_env", "access_key_secret_env"}:
                raise ValueError("配置字段不匹配。")
            if raw["access_key_id_env"] != "AccessKey_ID" or raw["access_key_secret_env"] != "AccessKey_Secret":
                raise ValueError("凭据变量名与用户约定不一致。")
            # 仅读取用户指定的进程环境变量，不使用文件密钥或自动凭据链。
            return cls.model_validate({"MG_OSS_REGION": raw["region"], "MG_OSS_ENDPOINT": raw["endpoint"],
                "MG_OSS_BUCKET": raw["bucket"], "MG_OSS_PREFIX": raw["prefix"],
                "MG_OSS_ACCESS_KEY_ID": os.environ.get(raw["access_key_id_env"]),
                "MG_OSS_ACCESS_KEY_SECRET": os.environ.get(raw["access_key_secret_env"])})
        except (OSError, UnicodeError, ValueError, TypeError, ValidationError):
            raise ServiceError(503, "OSS_CONFIG_INVALID", "OSS 配置或环境变量缺失，请核对 configs/oss.json 与 AccessKey_ID、AccessKey_Secret。") from None


def sdk_error(error):
    while isinstance(error, oss.exceptions.OperationError):
        error = error.unwrap()
    return error


def storage_error(error, stage: str) -> ServiceError:
    # SDK 异常可能包含签名请求，不能把异常字符串放到响应或日志中。
    error = sdk_error(error)
    status = getattr(error, "status_code", None)
    remote_code = getattr(error, "code", None)
    if remote_code == "InvalidAccessKeyId":
        code, message = "OSS_CREDENTIAL_INVALID", "OSS 无法识别当前 AccessKey ID，请核对环境凭据。"
    elif remote_code == "SignatureDoesNotMatch":
        code, message = "OSS_SIGNATURE_INVALID", "OSS 签名校验失败，请核对同一组 AccessKey 与 Secret。"
    elif status == 403:
        code, message = "OSS_PERMISSION_DENIED", "OSS 拒绝访问，请核对 RAM 授权与访问限制。"
    elif status == 404:
        code, message = ("OSS_BUCKET_NOT_FOUND", "OSS Bucket 不存在。") if remote_code == "NoSuchBucket" else ("OSS_OBJECT_NOT_FOUND", "OSS 对象不存在。")
    else:
        code, message = "OSS_REQUEST_FAILED", "OSS 请求失败，请核对网络、地域和配置。"
    result = ServiceError(503, code, f"{message}阶段：{stage}。")
    result.oss_code = remote_code
    result.oss_request_id = getattr(error, "request_id", None)
    return result


class OssStorage:
    def __init__(self, settings: OssSettings | None = None):
        self.settings = OssSettings.load() if settings is None else settings
        cfg = oss.config.load_default()
        cfg.region = self.settings.MG_OSS_REGION
        cfg.endpoint = self.settings.MG_OSS_ENDPOINT
        cfg.credentials_provider = oss.credentials.StaticCredentialsProvider(
            self.settings.MG_OSS_ACCESS_KEY_ID.get_secret_value(),
            self.settings.MG_OSS_ACCESS_KEY_SECRET.get_secret_value())
        cfg.retry_max_attempts = 1
        cfg.connect_timeout = 5
        cfg.readwrite_timeout = 15
        cfg.enabled_redirect = False
        self.client = oss.Client(cfg)

    @property
    def bucket(self):
        return self.settings.MG_OSS_BUCKET

    def _key(self, key: str):
        if not key.startswith(self.settings.MG_OSS_PREFIX) or ".." in key or "\\" in key:
            raise ServiceError(422, "OSS_KEY_INVALID", "对象键超出授权前缀。")

    def check_bucket(self):
        try:
            location = self.client.get_bucket_location(oss.GetBucketLocationRequest(bucket=self.bucket))
            acl = self.client.get_bucket_acl(oss.GetBucketAclRequest(bucket=self.bucket))
        except Exception as error:
            raise storage_error(error, "check_bucket") from None
        if location.location != "oss-" + self.settings.MG_OSS_REGION or acl.acl != "private":
            raise ServiceError(503, "OSS_BUCKET_INVALID", "Bucket 地域不匹配或权限不是私有。")
        return {"bucket": self.bucket, "region": self.settings.MG_OSS_REGION, "acl": acl.acl}

    def head(self, key: str):
        self._key(key)
        try:
            return self.client.head_object(oss.HeadObjectRequest(bucket=self.bucket, key=key))
        except Exception as error:
            inner = sdk_error(error)
            if getattr(inner, "status_code", None) == 404 and getattr(inner, "code", None) == "NoSuchKey":
                return None
            raise storage_error(error, "head_object") from None

    def read_verified(self, key: str, size: int, digest: str) -> bytes:
        self._key(key)
        try:
            acl = self.client.get_object_acl(oss.GetObjectAclRequest(bucket=self.bucket, key=key))
            if acl.acl not in ("private", "default"):
                raise ServiceError(503, "OSS_OBJECT_PUBLIC", "对象 ACL 不符合私有存储约定。")
            result = self.client.get_object(oss.GetObjectRequest(bucket=self.bucket, key=key))
            try:
                if result.content_length != size or size > MAX_FILE_BYTES:
                    raise ServiceError(503, "OSS_CONTENT_MISMATCH", "OSS 原文长度与版本记录不一致。")
                content = bytearray()
                for part in result.body.iter_bytes(chunk_size=65536):
                    content.extend(part)
                    if len(content) > size:
                        raise ServiceError(503, "OSS_CONTENT_MISMATCH", "OSS 原文超出版本长度。")
                data = bytes(content)
            finally:
                result.body.close()
        except ServiceError:
            raise
        except Exception as error:
            raise storage_error(error, "read_object") from None
        if len(data) != size or sha256(data).hexdigest() != digest:
            raise ServiceError(503, "OSS_CONTENT_MISMATCH", "OSS 原文长度或 SHA-256 与版本记录不一致。")
        return data

    def store_verified(self, key: str, data: bytes, content_type: str, digest: str) -> str:
        self._key(key)
        if not 0 < len(data) <= MAX_FILE_BYTES or sha256(data).hexdigest() != digest:
            raise ServiceError(422, "DOCUMENT_CONTENT_INVALID", "文件长度或哈希非法。")
        existing = self.head(key)
        if existing is None:
            try:
                self.client.put_object(oss.PutObjectRequest(bucket=self.bucket, key=key, body=data,
                    content_type=content_type, metadata={"sha256": digest}, forbid_overwrite=True))
            except Exception as error:
                # 并发旧领取者可能刚写入同一不可变对象；只在明确覆盖冲突时核对原文。
                inner = sdk_error(error)
                if not (getattr(inner, "status_code", None) == 409 and getattr(inner, "code", None) == "FileAlreadyExists"):
                    raise storage_error(error, "put_object") from None
        self.read_verified(key, len(data), digest)
        saved = self.head(key)
        if saved is None or not saved.etag:
            raise ServiceError(503, "OSS_OBJECT_NOT_FOUND", "上传核对后原文不可用，不能标记上传完成。")
        return saved.etag

    def delete_verification(self, key: str):
        if not key.startswith(self.settings.MG_OSS_PREFIX + "verification/"):
            raise ServiceError(422, "OSS_DELETE_FORBIDDEN", "只允许清理本轮连通性验证对象。")
        self._key(key)
        try:
            self.client.delete_object(oss.DeleteObjectRequest(bucket=self.bucket, key=key))
        except Exception as error:
            raise storage_error(error, "delete_verification") from None
