"""本人分析任务入口；范围和幂等键由服务器验证。"""

from uuid import UUID
from fastapi import Depends, Query, Request, Response
from pydantic import Field, field_validator, model_validator
from typing import Literal
from .tool_runtime import TrendArguments
from .report_contract import one_click_question
from .schemas import StrictRequest
from .index_routes import SearchRequest, PublishRequest
from . import agent_service as service


class OneClickOptions(TrendArguments):
    include_knowledge: bool = False


class AgentRequest(StrictRequest):
    question: str = Field(min_length=1, max_length=1000)
    engine_key: str | None = Field(default=None, min_length=1, max_length=96)
    end_cycle: int | None = Field(default=None, ge=1)
    idempotency_key: UUID
    workflow: Literal["agent", "one_click"] = "agent"
    workflow_options: OneClickOptions | None = None

    @model_validator(mode="after")
    def workflow_fields(self):
        if self.workflow == "agent" and self.workflow_options is not None:
            raise ValueError("自由问题分析不能携带一键流程参数。")
        if self.workflow == "one_click":
            if self.workflow_options is None or self.engine_key is None or self.end_cycle is None:
                raise ValueError("一键分析必须明确设备、末端周期和趋势参数。")
            if self.question != one_click_question(self.workflow_options.model_dump()):
                raise ValueError("一键分析问题必须与选定固定流程一致。")
        return self

    @field_validator("question")
    @classmethod
    def question_text(cls, value):
        return SearchRequest.valid_query(value)

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def key_uuid(cls, value):
        return PublishRequest.json_uuid(value)


def register_agent_routes(app, get_session, current, envelope):
    @app.get("/api/agent/runs/{run_id}/paths/{path_index}/{edge_index}/source")
    def path_source(request: Request, response: Response, run_id: UUID, path_index: int, edge_index: int,
                    principal=Depends(current), session=Depends(get_session)):
        row = service.owned_run(session, principal, run_id)
        response.headers["Cache-Control"] = "no-store"
        return envelope(request, service.path_source(session, row, path_index, edge_index))

    @app.post("/api/agent/runs", status_code=202)
    def submit(request: Request, response: Response, payload: AgentRequest,
               principal=Depends(current), session=Depends(get_session)):
        result = service.submit(session, principal, payload, app.state.device_runtime,
                                app.state.skill_registry, request.state.request_id)
        session.commit(); response.status_code = 200 if result["replayed"] else 202
        return envelope(request, result)

    @app.get("/api/agent/runs")
    def listing(request: Request, response: Response, offset: int = Query(0, ge=0), limit: int = Query(10, ge=1, le=50), deleted: bool = False,
                principal=Depends(current), session=Depends(get_session)):
        response.headers["Cache-Control"] = "no-store"
        return envelope(request, service.list_runs(session, principal, offset, limit, deleted))

    @app.get("/api/agent/runs/{run_id}")
    def detail(request: Request, response: Response, run_id: UUID,
               principal=Depends(current), session=Depends(get_session)):
        response.headers["Cache-Control"] = "no-store"
        return envelope(request, service.view(session, service.owned_run(session, principal, run_id)))

    @app.post("/api/agent/runs/{run_id}/delete")
    def delete(request: Request, run_id: UUID, principal=Depends(current), session=Depends(get_session)):
        result = service.record_lifecycle(session, principal, run_id, False, request.state.request_id)
        session.commit(); return envelope(request, result)

    @app.post("/api/agent/runs/{run_id}/restore")
    def restore(request: Request, run_id: UUID, principal=Depends(current), session=Depends(get_session)):
        result = service.record_lifecycle(session, principal, run_id, True, request.state.request_id)
        session.commit(); return envelope(request, result)

    @app.get("/api/agent/runs/{run_id}/events")
    def events(request: Request, response: Response, run_id: UUID, after: int = Query(0, ge=0),
               principal=Depends(current), session=Depends(get_session)):
        row = service.owned_run(session, principal, run_id)
        response.headers["Cache-Control"] = "no-store"
        return envelope(request, service.events(session, row, after))

    @app.post("/api/agent/runs/{run_id}/cancel")
    def cancel(request: Request, run_id: UUID, principal=Depends(current), session=Depends(get_session)):
        row = service.owned_run(session, principal, run_id)
        from .job_service import cancel_job
        cancel_job(session, principal, row.job_id, request.state.request_id)
        session.commit()
        return envelope(request, service.view(session, row))

    @app.get("/api/agent/runs/{run_id}/citations/{claim_index}/{citation_index}")
    def source(request: Request, response: Response, run_id: UUID, claim_index: int, citation_index: int,
               principal=Depends(current), session=Depends(get_session)):
        row = service.owned_run(session, principal, run_id)
        response.headers["Cache-Control"] = "no-store"
        return envelope(request, service.citation_source(session, row, claim_index, citation_index))
