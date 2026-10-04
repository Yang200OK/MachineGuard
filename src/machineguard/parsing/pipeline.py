"""保留精确字符位置与父页面的章节/token 切片；不使用文本搜索恢复位置。"""

from hashlib import sha256
import json
import re

import tiktoken

from machineguard.server.auth_service import ServiceError
from .adapters import parse_pdf


def _sections(text, file_format, title):
    if file_format != "md":
        return [(0, len(text), title)]
    headings = []; fenced = False; offset = 0
    for line in text.splitlines(keepends=True):
        if re.match(r"^\s*(```|~~~)", line):
            fenced = not fenced
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line.rstrip("\r\n")) if not fenced else None
        if match:
            headings.append((offset, match.group(1)))
        offset += len(line)
    starts = [(0, title)] if not headings or headings[0][0] > 0 else []
    starts += headings
    return [(start, starts[i + 1][0] if i + 1 < len(starts) else len(text), heading) for i, (start, heading) in enumerate(starts)]


def split_pages(pages, file_format, title, config):
    encoding = tiktoken.get_encoding(config.tokenizer)
    count = lambda value: len(encoding.encode_ordinary(value))
    text = ""; parent_pages = []
    for page in pages:
        if parent_pages:
            text += "\n\n"
        start = len(text); text += page["text"]
        parent_pages.append({**page, "char_start": start, "char_end": len(text)})
    if not text.strip():
        raise ServiceError(422, "PARSE_EMPTY", "没有可切片的正文，不以空文本继续入库。")
    if len(text) > config.max_text_chars:
        raise ServiceError(422, "PARSE_TEXT_LIMIT", "提取正文超过字符限额。")
    chunks = []
    for section_start, section_end, section in _sections(text, file_format, title):
        start = section_start
        while start < section_end:
            # 使用字符索引切原文并实际计算 BPE token，避免截断中文多字节 token。
            low, high = start + 1, min(section_end, start + config.chunk_tokens * 12)
            while low < high:
                mid = (low + high + 1) // 2
                if count(text[start:mid]) <= config.chunk_tokens:
                    low = mid
                else:
                    high = mid - 1
            end = low
            while count(text[start:end]) > config.chunk_tokens and end > start:
                end -= 1
            if end <= start:
                raise ServiceError(422, "PARSE_TOKEN_LIMIT", "正文无法在 token 限额内切片。")
            if end < section_end:
                boundaries = [m.end() + start for m in re.finditer(r"\n\s*\n", text[start:end])]
                useful = [value for value in boundaries if count(text[start:value]) >= 400]
                if useful:
                    end = useful[-1]
            body = text[start:end]
            if body.strip():
                spans = []
                for parent in parent_pages:
                    left, right = max(start, parent["char_start"]), min(end, parent["char_end"])
                    if left < right:
                        local_start = left - parent["char_start"]; local_end = right - parent["char_start"]
                        spans.append({"page": parent["number"], "char_start": local_start, "char_end": local_end,
                            "chunk_start": left - start, "chunk_end": right - start,
                            "line_start": parent["text"].count("\n", 0, local_start) + 1,
                            "line_end": parent["text"].count("\n", 0, max(local_start, local_end - 1)) + 1})
                if not spans:
                    raise ServiceError(422, "PARSE_LOCATION_INVALID", "切片缺少真实父页面。")
                chinese = bool(re.search(r"[\u4e00-\u9fff]", body)); english = bool(re.search(r"[A-Za-z]", body))
                chunks.append({"ordinal": len(chunks) + 1, "text": body, "sha256": sha256(body.encode()).hexdigest(),
                    "token_count": count(body), "char_start": start, "char_end": end, "page": spans[0]["page"],
                    "page_spans": spans, "section": section[:200], "language": "mixed" if chinese and english else "zh" if chinese else "en" if english else "other"})
                if len(chunks) > config.max_chunks:
                    raise ServiceError(422, "PARSE_CHUNK_LIMIT", "切片数量超过限额。")
            if end == section_end:
                break
            low, high = start + 1, end
            while low < high:
                mid = (low + high) // 2
                if count(text[mid:end]) <= config.overlap_tokens:
                    high = mid
                else:
                    low = mid + 1
            start = low
    return parent_pages, chunks


def parse_document(data, file_format, mode, title, config):
    if mode == "vision" and file_format != "pdf":
        raise ServiceError(422, "PARSE_MODE_INVALID", "视觉解析只接受 PDF。")
    if file_format == "pdf":
        pages = parse_pdf(data, mode, config)
    else:
        try:
            text = data.decode("utf-8-sig", errors="strict")
        except UnicodeError:
            raise ServiceError(422, "PARSE_ENCODING_INVALID", "文本原文不是 UTF-8。") from None
        pages = [{"number": 1, "text": text, "blocks": [], "warnings": [], "adapter": "utf8_text", "model": None, "usage": None}]
    pages, chunks = split_pages(pages, file_format, title, config)
    result = {"pages": pages, "chunks": chunks}
    result["result_sha256"] = sha256(json.dumps(result, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    return result
