"""受控文档 HTTP 入口；先授权再读取有上限的 multipart 文件。"""

from urllib.parse import quote
from uuid import UUID

from fastapi import Depends, Query, Request, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException

from . import document_service as service
from .auth_service import ServiceError
from .document_schemas import SourceMetadata
from .oss_storage import MAX_FILE_BYTES


async def parse_upload(request: Request):
    if not request.headers.get("content-type", "").lower().startswith("multipart/form-data;"):
        raise ServiceError(415, "DOCUMENT_MULTIPART_REQUIRED", "文档上传必须使用 multipart/form-data。")
    # 对实际接收字节计数，不依赖客户端 Content-Length，也不接受无限流式文件。
    body = bytearray()
    async for part in request.stream():
        body.extend(part)
        if len(body) > MAX_FILE_BYTES + 65536:
            raise ServiceError(413, "DOCUMENT_TOO_LARGE", "文件和上传元数据超出请求上限。")
    async def receive():
        return {"type": "http.request", "body": bytes(body), "more_body": False}
    parsed_request = Request(request.scope, receive)
    try:
        async with parsed_request.form(max_files=1, max_fields=2, max_part_size=32768) as form:
            if len(form.multi_items()) != 3 or set(form.keys()) != {"file", "metadata", "idempotency_key"}:
                raise ServiceError(422, "DOCUMENT_FIELDS_INVALID", "仅接受 file、metadata 与 idempotency_key 三个字段。")
            file, raw_metadata, raw_key = form["file"], form["metadata"], form["idempotency_key"]
            if not isinstance(file, UploadFile) or not isinstance(raw_metadata, str) or not isinstance(raw_key, str):
                raise ServiceError(422, "DOCUMENT_FIELDS_INVALID", "上传字段类型非法。")
            metadata = SourceMetadata.model_validate_json(raw_metadata)
            key = UUID(raw_key)
            data = await file.read(MAX_FILE_BYTES + 1)
            return file.filename or "", data, metadata, key
    except (ValidationError, ValueError, MultiPartException):
        raise ServiceError(422, "DOCUMENT_METADATA_INVALID", "来源元数据、请求键或 multipart 格式不符合约束。") from None


def register_document_routes(app, get_session, current, envelope):
    @app.get("/api/knowledge/documents")
    def documents(request: Request, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),
                  principal=Depends(current), session: Session = Depends(get_session)):
        return envelope(request, service.list_documents(session, principal, offset, limit))

    @app.get("/api/knowledge/documents/{document_id}")
    def detail(request: Request, document_id: UUID, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),
               principal=Depends(current), session: Session = Depends(get_session)):
        return envelope(request, service.document_detail(session, principal, document_id, offset, limit))

    async def submit(request, response, principal, session, target=None):
        principal.require("knowledge:manage")
        filename, data, metadata, key = await parse_upload(request)
        result, existing = await run_in_threadpool(service.upload, session, principal, filename, data, metadata, key,
                                                  request.state.request_id, target)
        response.status_code = 200 if existing else 201
        return envelope(request, result)

    @app.post("/api/knowledge/documents", status_code=201)
    async def create(request: Request, response: Response, principal=Depends(current), session: Session = Depends(get_session)):
        return await submit(request, response, principal, session)

    @app.post("/api/knowledge/documents/{document_id}/versions", status_code=201)
    async def version(request: Request, response: Response, document_id: UUID,
                      principal=Depends(current), session: Session = Depends(get_session)):
        return await submit(request, response, principal, session, document_id)

    @app.get("/api/knowledge/versions/{version_id}/content")
    def original(version_id: UUID, principal=Depends(current), session: Session = Depends(get_session)):
        filename, suffix, data = service.read_original(session, principal, version_id)
        return Response(data, media_type="application/octet-stream", headers={"Cache-Control": "no-store",
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename, safe=""),
            "X-Content-Type-Options": "nosniff"})
