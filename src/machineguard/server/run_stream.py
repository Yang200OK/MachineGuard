"""只推送已提交事件；长连接按批次重新鉴权并释放数据库会话。"""

import asyncio
import json
import time
from uuid import UUID
from fastapi import Header, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import SQLAlchemyError
from . import auth_service, agent_service, report_service
from .auth_service import ServiceError
from .job_models import Job


def frame(kind, data, sequence=None):
    prefix = f"id: {sequence}\n" if sequence is not None else ""
    return prefix + f"event: {kind}\ndata: " + json.dumps(agent_service.jsonable(data), ensure_ascii=False, allow_nan=False) + "\n\n"


def read_batch(database, token, run_id, after, review):
    with database.sessions() as session:
        principal = auth_service.authenticate(session, token)
        row = report_service.review_row(session, principal, run_id)[0] if review else agent_service.owned_run(session, principal, run_id)
        if after > row.event_sequence:
            raise ServiceError(409, "STREAM_CURSOR_AHEAD", "事件序号超出已保存范围，请重新读取此运行。")
        batch = agent_service.events(session, row, after)
        job = session.get(Job, row.job_id)
        state = {"run_id": str(row.id), "event_sequence": row.event_sequence,
                 "status": job.status, "stage": job.stage, "progress": job.progress,
                 "cancel_requested": job.cancel_requested_at is not None,
                 "review": report_service.review_summary(session, row)}
        return batch, state


def register_stream_routes(app):
    async def response(request, run_id, after, last_event_id, review):
        scheme, _, token = request.headers.get("Authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise ServiceError(401, "SESSION_REQUIRED", "事件流需要当前登录会话。")
        if last_event_id is not None:
            if not last_event_id.isascii() or not last_event_id.isdigit() or len(last_event_id) > 10 or int(last_event_id) > 2147483647:
                raise ServiceError(422, "STREAM_CURSOR_INVALID", "Last-Event-ID 必须为有效非负事件序号。")
            if after and after != int(last_event_id):
                raise ServiceError(422, "STREAM_CURSOR_INVALID", "请求中的事件序号不一致。")
            after = int(last_event_id)
        database = request.app.state.database
        initial = await asyncio.to_thread(read_batch, database, token, run_id, after, review)

        async def stream():
            cursor = after; snapshot = None; started = time.monotonic(); batch, state = initial
            try:
                while not await request.is_disconnected():
                    for event in batch["items"]:
                        yield frame("run-event", event, event["sequence"])
                        cursor = event["sequence"]
                    if batch["has_more"]:
                        batch, state = await asyncio.to_thread(read_batch, database, token, run_id, cursor, review)
                        continue
                    signature = json.dumps(agent_service.jsonable(state), sort_keys=True)
                    if signature != snapshot:
                        yield frame("state", state); snapshot = signature
                    if state["status"] in ("succeeded", "failed", "cancelled"):
                        yield frame("done", state); return
                    if time.monotonic() - started >= 55:
                        yield frame("reconnect", {"after": cursor}); return
                    yield ": heartbeat\n\n"
                    await asyncio.sleep(1)
                    batch, state = await asyncio.to_thread(read_batch, database, token, run_id, cursor, review)
            except ServiceError as error:
                yield frame("error", {"code": error.code, "message": error.message, "status": error.status})
            except SQLAlchemyError:
                yield frame("error", {"code": "STREAM_DATABASE_ERROR", "message": "事件流数据库读取失败，请重新连接核对。", "status": 503})
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.get("/api/agent/runs/{run_id}/stream")
    async def owner(request: Request, run_id: UUID, after: int = Query(0, ge=0, le=2147483647), last_event_id: str | None = Header(None)):
        return await response(request, run_id, after, last_event_id, False)

    @app.get("/api/reviews/{run_id}/stream")
    async def reviewer(request: Request, run_id: UUID, after: int = Query(0, ge=0, le=2147483647), last_event_id: str | None = Header(None)):
        return await response(request, run_id, after, last_event_id, True)
