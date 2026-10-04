"""按本人回答的固定引用读取有界原文上下文，不接受客户端提交的引文。"""

from hashlib import sha256
from uuid import UUID
from sqlalchemy import select
from .auth_service import ServiceError
from .document_models import Document, DocumentVersion
from .job_models import Job
from .knowledge_service import review_state
from .parse_models import DocumentChunk, DocumentPage, DocumentParse
from .qa_service import owned_answer


def citation_source(session, principal, answer_id, claim_index, citation_index):
    answer = owned_answer(session, principal, answer_id)
    if session.get(Job, answer.job_id).status != "succeeded" or answer.result is None:
        raise ServiceError(409, "QA_RESULT_UNAVAILABLE", "该回答尚无已保存的引用。")
    claims = answer.result["claims"]
    if not 0 <= claim_index < len(claims) or not 0 <= citation_index < len(claims[claim_index]["citations"]):
        raise ServiceError(404, "QA_CITATION_NOT_FOUND", "该回答的引用不存在。")
    citation = claims[claim_index]["citations"][citation_index]
    return checked_citation_source(session, citation)


def checked_citation_source(session, citation):
    """调用方先核对本人结果；保持既有历史原文和页面校验逻辑。"""
    chunk = session.get(DocumentChunk, UUID(citation["chunk_id"]))
    parsed = session.get(DocumentParse, UUID(citation["parse_id"]))
    version = session.get(DocumentVersion, UUID(citation["version_id"]))
    # 只核对当时的解析和位置，不要求历史版本仍可作为新回答的证据。
    if chunk is None or parsed is None or version is None:
        raise ServiceError(409, "QA_SOURCE_UNAVAILABLE", "历史原文记录不可获取，已保存的引用仍保留。")
    start = citation["char_start"] - chunk.char_start
    end = citation["char_end"] - chunk.char_start
    if (chunk.parse_id != parsed.id or parsed.version_id != version.id or chunk.document_version != version.id
            or str(chunk.source_id) != citation["source_id"] or version.number != citation["document_version"]
            or start < 0 or end > len(chunk.text) or start >= end or chunk.text[start:end] != citation["quote"]
            or sha256(chunk.text.encode("utf-8")).hexdigest() != chunk.sha256):
        raise ServiceError(409, "QA_SOURCE_LOCATION_INVALID", "历史引文与原文或位置不一致，未展示替代版本。")
    expected = []
    for span in chunk.page_spans:
        lo, hi = max(start, span["chunk_start"]), min(end, span["chunk_end"])
        if lo < hi:
            expected.append({"page": span["page"], "char_start": span["char_start"] + lo - span["chunk_start"],
                             "char_end": span["char_start"] + hi - span["chunk_start"]})
    if expected != citation["page_spans"]:
        raise ServiceError(409, "QA_SOURCE_LOCATION_INVALID", "历史引文的页面映射不一致。")
    contexts = []
    for span in expected:
        page = session.scalar(select(DocumentPage).where(DocumentPage.parse_id == parsed.id,
                                                       DocumentPage.number == span["page"]))
        if page is None:
            raise ServiceError(409, "QA_SOURCE_UNAVAILABLE", "历史引文对应的页面不可获取。")
        lo, hi = span["char_start"] - page.char_start, span["char_end"] - page.char_start
        chunk_span = next((s for s in chunk.page_spans if s["page"] == span["page"]
                          and s["char_start"] <= span["char_start"] and s["char_end"] >= span["char_end"]), None)
        if chunk_span is None:
            raise ServiceError(409, "QA_SOURCE_LOCATION_INVALID", "历史切片的字符映射不一致。")
        chunk_lo = chunk_span["chunk_start"] + span["char_start"] - chunk_span["char_start"]
        if lo < 0 or hi > len(page.text) or page.text[lo:hi] != chunk.text[chunk_lo:chunk_lo + hi - lo]:
            raise ServiceError(409, "QA_SOURCE_LOCATION_INVALID", "历史页面与切片引文不一致。")
        contexts.append({**span, "before": page.text[max(0, lo - 220):lo], "quote": page.text[lo:hi],
                         "after": page.text[hi:hi + 220]})
    return {"citation": citation, "contexts": contexts, "filename": version.filename,
            "file_format": version.file_format, "is_active": version.is_active,
            "is_archived": session.get(Document, version.document_id).archived_at is not None,
            "review_status": review_state(session, parsed)["review_status"],
            "management_revision": version.management_revision}
