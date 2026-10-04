"""实际检查私有 OSS 上传、读回、匿名拒绝与验证文件清理，不输出密钥。"""

from hashlib import sha256
import json
from pathlib import Path
import sys
from uuid import uuid4

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from machineguard.server.auth_service import ServiceError
from machineguard.server.oss_storage import OssStorage


def main():
    store = None
    key = None
    error = None
    result = None
    try:
        store = OssStorage()
        result = store.check_bucket()
        key = store.settings.MG_OSS_PREFIX + "verification/" + uuid4().hex + ".txt"
        data = b"MachineGuard OSS connectivity verification\n"
        digest = sha256(data).hexdigest()
        etag = store.store_verified(key, data, "text/plain; charset=utf-8", digest)
        url = f"https://{store.bucket}.oss-{store.settings.MG_OSS_REGION}.aliyuncs.com/{key}"
        # 不发送 Authorization，也不跟随重定向或从配置取匿名请求凭据。
        response = requests.get(url, timeout=(5, 15), allow_redirects=False)
        try:
            if response.status_code != 403:
                raise ServiceError(503, "OSS_ANONYMOUS_ACCESS_INVALID", "匿名访问未返回 403，私有访问验证失败。")
        finally:
            response.close()
        result.update(verified=True, size_bytes=len(data), sha256=digest, etag=etag, anonymous_status=403)
    except ServiceError as caught:
        error = {"code": caught.code, "message": caught.message,
                 "oss_code": getattr(caught, "oss_code", None), "oss_request_id": getattr(caught, "oss_request_id", None)}
    except Exception:
        error = {"code": "OSS_VERIFICATION_FAILED", "message": "OSS 实际验证请求失败，未输出签名与凭据。"}
    finally:
        if store is not None and key is not None:
            try:
                store.delete_verification(key)
                if store.head(key) is not None:
                    raise ValueError("验证对象未清理。")
                if result is not None:
                    result["verification_object_cleaned"] = True
            except Exception:
                error = {"code": "OSS_CLEANUP_FAILED", "message": "验证对象清理失败，请核对验证目录的删除权限。", "object_key": key,
                         "prior_error": error}
    output = {"error": error} if error else {"data": result}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 1 if error else 0


if __name__ == "__main__":
    raise SystemExit(main())
