"""Worker 租约与阶段检查独立于 API，数据库失联时明确退出。"""

import logging
from threading import Event, Thread

from sqlalchemy.exc import SQLAlchemyError

from machineguard.server import job_service
from machineguard.server.auth_service import ServiceError
from machineguard.server.parse_service import finish_parse
from machineguard.server.index_service import finish_publication
from machineguard.server.qa_service import finish_answer
from machineguard.server.agent_service import finish_agent
from machineguard.server.delivery_service import finish_export
from machineguard.training import TrainingError
from .handlers import HANDLERS, REPLAYABLE_KINDS


LOGGER = logging.getLogger("machineguard.worker")


class JobContext:
    def __init__(self, database, config, lease):
        self.database, self.config, self.lease = database, config, lease
        self.done = Event()
        self.interrupted = Event()
        self.failure = None
        self.thread = Thread(target=self._heartbeat, name="job-heartbeat", daemon=True)

    def _heartbeat(self):
        while not self.done.wait(self.config.heartbeat_seconds):
            try:
                with self.database.sessions.begin() as session:
                    alive = job_service.heartbeat(session, self.lease, self.config)
                if not alive:
                    self.interrupted.set()
                    return
            except Exception as error:
                self.failure = type(error).__name__
                self.interrupted.set()
                return

    def stage(self, name, progress):
        if self.interrupted.is_set():
            raise job_service.JobInterrupted()
        with self.database.sessions.begin() as session:
            alive = job_service.heartbeat(session, self.lease, self.config, name, progress)
        if not alive:
            self.interrupted.set()
            raise job_service.JobInterrupted()
        LOGGER.info("job_id=%s attempt=%s stage=%s progress=%s", self.lease.job_id, self.lease.number, name, progress)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.done.set()
        self.thread.join()
        if self.failure is not None:
            raise RuntimeError(f"任务心跳失败（{self.failure}）；停止 Worker，租约到期后由队列明确处理。") from None


def execute(database, config, lease):
    LOGGER.info("job_id=%s attempt=%s stage=claimed", lease.job_id, lease.number)
    with JobContext(database, config, lease) as context:
        try:
            result = HANDLERS[lease.kind](context, lease.payload)
        except job_service.JobInterrupted:
            LOGGER.info("job_id=%s attempt=%s execution=interrupted", lease.job_id, lease.number)
            return
        except SQLAlchemyError:
            # 连接故障不能误记成模型校验失败，也不能继续工作或回退到其它存储。
            raise RuntimeError("任务数据库操作失败；停止 Worker，待数据库恢复后处理到期租约。") from None
        except Exception as error:
            if lease.kind in ("document_parse", "document_visual_parse", "knowledge_index", "knowledge_answer", "agent_run", "report_export") and isinstance(error, ServiceError):
                code, message = error.code, error.message
            else:
                code = "MODEL_INTEGRITY_FAILED" if isinstance(error, (TrainingError, ServiceError)) else "JOB_HANDLER_FAILED"
                message = "模型文件、配置或登记校验失败，请按任务阶段核对产物。" if code == "MODEL_INTEGRITY_FAILED" else "任务处理器执行失败，请按任务编号排查。"
            LOGGER.error("job_id=%s attempt=%s handler_error=%s", lease.job_id, lease.number, type(error).__name__)
            with database.sessions.begin() as session:
                saved = job_service.finish_job(session, lease, error_code=code, error_message=message)
        else:
            # 写入终态仍在行锁内检查有效租约和取消请求；旧 Worker 无权覆盖新执行。
            try:
                with database.sessions.begin() as session:
                    if lease.kind in ("document_parse", "document_visual_parse"):
                        saved = finish_parse(session, lease, result)
                    elif lease.kind == "knowledge_index":
                        saved = finish_publication(session, lease, result)
                    elif lease.kind == "knowledge_answer":
                        saved = finish_answer(session, lease, result)
                    elif lease.kind == "report_export":
                        saved = finish_export(session, lease, result)
                    elif lease.kind == "agent_run":
                        saved = finish_agent(session, lease, result)
                    else:
                        saved = job_service.finish_job(session, lease, result=result)
            except ServiceError as error:
                if lease.kind not in ("document_parse", "document_visual_parse", "knowledge_index", "knowledge_answer", "agent_run", "report_export"):
                    raise
                # 来源提交检查失败时先回滚页面/切片，再以同一有效租约明确记录失败。
                with database.sessions.begin() as session:
                    saved = job_service.finish_job(session, lease, error_code=error.code, error_message=error.message)
        LOGGER.info("job_id=%s attempt=%s terminal_saved=%s", lease.job_id, lease.number, saved)


def tick(database, config, worker_id):
    with database.sessions.begin() as session:
        recovered = job_service.recover_expired(session, REPLAYABLE_KINDS)
    if recovered:
        LOGGER.info("worker_id=%s expired_jobs_processed=%s", worker_id, recovered)
    with database.sessions.begin() as session:
        lease = job_service.claim_job(session, worker_id, tuple(HANDLERS), config)
    if lease is None:
        return False
    execute(database, config, lease)
    return True
