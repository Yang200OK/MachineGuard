"""单轮及会话问答提交，服务端冻结本人历史和固定范围。"""

from uuid import UUID
from fastapi import Depends, Query, Request, Response
from pydantic import Field, field_validator, model_validator
from .index_routes import SearchRequest, PublishRequest
from .schemas import StrictRequest
from .job_config import load_worker_configuration
from . import qa_service as service
from . import conversation_service


class AnswerRequest(StrictRequest):
    query: str = Field(min_length=1, max_length=1000)
    snapshot_id: UUID | None = None
    equipment_type: str | None = Field(default=None, min_length=1, max_length=200)
    idempotency_key: UUID
    conversation_id: UUID | None = None
    expected_revision: int | None = Field(default=None, ge=0, le=20)
    # 复用既有严格查询校验；候选数量由冻结配置控制，客户端不可指定。

    @field_validator("query")
    @classmethod
    def valid_query(cls, value):
        return SearchRequest.valid_query(value)

    @field_validator("snapshot_id", "conversation_id", mode="before")
    @classmethod
    def snapshot_uuid(cls, value):
        return SearchRequest.json_uuid(value)

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def key_uuid(cls, value):
        return PublishRequest.json_uuid(value)

    @model_validator(mode="after")
    def conversation_scope(self):
        if (self.conversation_id is None) != (self.expected_revision is None):
            raise ValueError("会话编号和预期轮次必须同时提供。")
        if self.conversation_id is not None and (self.snapshot_id is not None or self.equipment_type is not None):
            raise ValueError("会话快照和设备类型已固定，不能在轮次中更改。")
        return self


class ConversationRequest(StrictRequest):
    title: str = Field(min_length=1, max_length=100)
    snapshot_id: UUID | None = None
    equipment_type: str | None = Field(default=None, min_length=1, max_length=200)
    engine_key: str | None = Field(default=None, min_length=1, max_length=96)
    end_cycle: int | None = Field(default=None, ge=1)
    idempotency_key: UUID

    @field_validator("title")
    @classmethod
    def title_text(cls, value):
        return SearchRequest.valid_query(value)

    @field_validator("snapshot_id", mode="before")
    @classmethod
    def snapshot_uuid(cls, value):
        return SearchRequest.json_uuid(value)

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def key_uuid(cls, value):
        return PublishRequest.json_uuid(value)

    @model_validator(mode="after")
    def paired_device(self):
        if (self.engine_key is None) != (self.end_cycle is None):
            raise ValueError("设备代码和观测周期必须同时提供。")
        return self


class ConversationTitleRequest(StrictRequest):
    title: str = Field(min_length=1, max_length=100)
    expected_title: str = Field(min_length=1, max_length=100)

    @field_validator("title", "expected_title")
    @classmethod
    def title_text(cls, value):
        return SearchRequest.valid_query(value)


def register_qa_routes(app, get_session, current, envelope):
    @app.post("/api/conversations", status_code=201)
    def create(request: Request, response: Response, payload: ConversationRequest,
               principal=Depends(current), session=Depends(get_session)):
        result = conversation_service.create_conversation(session, principal, payload, request.state.request_id)
        session.commit(); response.status_code = 200 if result["replayed"] else 201
        return envelope(request, result)

    @app.get("/api/conversations")
    def conversations(request: Request, response: Response, offset: int = Query(0, ge=0), limit: int = Query(10, ge=1, le=50),
                      search: str = Query("", max_length=100), deleted: bool = False,
                      principal=Depends(current), session=Depends(get_session)):
        response.headers["Cache-Control"] = "no-store"
        return envelope(request, conversation_service.list_conversations(session, principal, offset, limit, search, deleted))

    @app.patch("/api/conversations/{conversation_id}/title")
    def rename(request: Request, response: Response, conversation_id: UUID, payload: ConversationTitleRequest,
               principal=Depends(current), session=Depends(get_session)):
        result = conversation_service.rename_conversation(session, principal, conversation_id, payload.title,
                                                          payload.expected_title, request.state.request_id)
        session.commit(); response.headers["Cache-Control"] = "no-store"
        return envelope(request, result)

    @app.get("/api/chat/{answer_id}/citations/{claim_index}/{citation_index}")
    def source(request: Request, response: Response, answer_id: UUID,
               claim_index: int, citation_index: int, principal=Depends(current), session=Depends(get_session)):
        from .qa_source import citation_source
        if not 0 <= claim_index < 6 or not 0 <= citation_index < 5:
            from .auth_service import ServiceError
            raise ServiceError(404, "QA_CITATION_NOT_FOUND", "该回答的引用不存在。")
        response.headers["Cache-Control"] = "no-store"
        return envelope(request, citation_source(session, principal, answer_id, claim_index, citation_index))

    @app.get("/api/conversations/{conversation_id}")
    def conversation(request: Request, response: Response, conversation_id: UUID,
                     principal=Depends(current), session=Depends(get_session)):
        response.headers["Cache-Control"] = "no-store"
        return envelope(request, conversation_service.detail(session, principal, conversation_id))

    @app.post("/api/chat", status_code=202)
    def submit(request: Request, response: Response, payload: AnswerRequest,
               principal=Depends(current), session=Depends(get_session)):
        result = service.submit_answer(session, principal, payload, load_worker_configuration(), request.state.request_id)
        session.commit()
        response.status_code = 200 if result["replayed"] else 202
        return envelope(request, result)

    @app.get("/api/chat")
    def listing(request: Request, response: Response, offset: int = Query(0, ge=0), limit: int = Query(10, ge=1, le=50),
                principal=Depends(current), session=Depends(get_session)):
        response.headers["Cache-Control"] = "no-store"
        return envelope(request, service.list_answers(session, principal, offset, limit))

    @app.get("/api/chat/{answer_id}")
    def detail(request: Request, response: Response, answer_id: UUID, principal=Depends(current), session=Depends(get_session)):
        response.headers["Cache-Control"] = "no-store"
        return envelope(request, service.answer_view(session, service.owned_answer(session, principal, answer_id)))
