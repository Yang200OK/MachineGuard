"""只读查询配置环境凭据对应的 RAM 身份，不输出密钥与完整账号标识。"""

import json
from pathlib import Path
import re
import sys

from alibabacloud_sts20150401.client import Client
from alibabacloud_tea_openapi.models import Config
from alibabacloud_tea_util.models import RuntimeOptions

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from machineguard.server.auth_service import ServiceError
from machineguard.server.oss_storage import OssSettings


def main():
    try:
        settings = OssSettings.load()
        client = Client(Config(access_key_id=settings.MG_OSS_ACCESS_KEY_ID.get_secret_value(),
            access_key_secret=settings.MG_OSS_ACCESS_KEY_SECRET.get_secret_value(), endpoint="sts.cn-hangzhou.aliyuncs.com"))
        result = client.get_caller_identity_with_options(RuntimeOptions(autoretry=False, connect_timeout=5000, read_timeout=15000)).body
        if result.identity_type != "RAMUser" or not result.arn or ":user/" not in result.arn:
            raise ServiceError(503, "OSS_IDENTITY_NOT_RAM_USER", "当前凭据不是项目要求的专用 RAM 用户。")
        data = {"credential_source": "process_environment", "environment_names": ["AccessKey_ID", "AccessKey_Secret"],
            "identity_type": result.identity_type, "ram_user": result.arn.split(":user/", 1)[1],
            "account_suffix": result.account_id[-4:], "request_id": result.request_id}
        print(json.dumps({"data": data}, ensure_ascii=False, indent=2))
        return 0
    except ServiceError as error:
        output = {"code": error.code, "message": error.message}
    except Exception as error:
        code = getattr(error, "code", None)
        output = {"code": "OSS_IDENTITY_CHECK_FAILED", "message": "STS 身份查询失败，请核对凭据和网络。",
                  "sts_code": code if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", code) else None}
    print(json.dumps({"error": output}, ensure_ascii=False, indent=2))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
