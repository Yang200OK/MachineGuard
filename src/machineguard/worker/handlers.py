"""显式任务白名单；客户端不能指定脚本、路径或任意函数。"""

from machineguard.model_artifacts import verify_manifest
from machineguard.server.auth_service import ServiceError
from machineguard.server.business_config import load_business_configuration
from machineguard.server.device_service import DeviceRuntime
from machineguard.training import TrainingError
from .document_handler import document_parse
from .index_handler import knowledge_index
from .qa_handler import knowledge_answer
from .agent_handler import agent_run
from .export_handler import report_export


def model_integrity_check(context, payload):
    context.stage("verify_manifest", 20)
    runtime = DeviceRuntime(load_business_configuration())
    manifest = runtime.predictor.manifest
    if manifest["model_version"] != payload["model_version"] or runtime.manifest_sha256 != payload["manifest_sha256"]:
        raise TrainingError("任务提交后部署模型已经变化，不能以新模型替代原任务目标。")
    context.stage("verify_registration", 70)
    with context.database.sessions() as session:
        registered = runtime.check_registration(session)
        if registered.weight_sha256 != payload["weight_sha256"]:
            raise ServiceError(503, "MODEL_CHANGED", "模型登记与任务固定的权重哈希不一致。")
    # 完成前再次核对磁盘清单，避免把执行期间变化的文件标记为已验证。
    if verify_manifest(runtime.config.bundle_path, "machineguard.lstm_deployment.v1") != manifest:
        raise TrainingError("校验期间部署文件发生变化。")
    context.stage("verified", 95)
    return {"model_version": manifest["model_version"], "completed_epochs": manifest["completed_epochs"],
            "best_epoch": manifest["best_epoch"], "manifest_sha256": runtime.manifest_sha256,
            "weight_sha256": payload["weight_sha256"], "files": manifest["files"],
            "device": "cpu", "verified": True,
            "checks": ["manifest_sha256", "file_sha256", "cpu_weight_load", "preprocessing", "published_registration"],
            "limitation": "只验证已发布产物完整性，不重新训练、不读取官方测试标签或生成新预测。"}


HANDLERS = {"model_integrity_check": model_integrity_check, "document_parse": document_parse,
            "document_visual_parse": document_parse, "knowledge_index": knowledge_index, "knowledge_answer": knowledge_answer,
            "agent_run": agent_run, "report_export": report_export}
# 原生解析只读源对象，结果与任务终态原子提交，可在失联后重领。
# 视觉请求可能产生费用，失联后明确失败，仅由用户显式创建新解析，不自动重复调用。
# 知识向量同样不自动重领，防止失联后重复收费；已完成快照可直接读取。
# 单轮问答重排与生成同样只执行一次，响应丢失时读取原请求记录。
REPLAYABLE_KINDS = frozenset({"model_integrity_check", "document_parse", "report_export"})
