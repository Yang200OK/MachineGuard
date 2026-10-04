"""导出只读取冻结报告；对象回读校验后才允许提交下载记录。"""
from hashlib import sha256
from uuid import UUID
from machineguard.server.delivery_service import execution_export
from machineguard.server.report_render import export_document, render_report, MIME
from machineguard.server.oss_storage import OssStorage
from machineguard.server.auth_service import ServiceError


def report_export(context, payload):
    context.stage("render_report", 20)
    with context.database.sessions() as session:
        row, report = execution_export(session, UUID(payload["export_id"]))
        if row.job_id != context.lease.job_id:
            raise ServiceError(409, "EXPORT_RESULT_INVALID", "导出任务不匹配。")
        document = export_document(report); config = dict(row.render_config); format = row.format
    data = render_report(document, format, config)
    digest = sha256(data).hexdigest()
    context.stage("upload_report", 70)
    storage = OssStorage()
    key = f"{storage.settings.MG_OSS_PREFIX}exports/{payload['report_id']}/{payload['export_id']}/{digest}.{format}"
    etag = storage.store_verified(key, data, MIME[format], digest)
    context.stage("report_export_verified", 95)
    return {"key": key, "sha256": digest, "size": len(data), "etag": etag, "format": format}
