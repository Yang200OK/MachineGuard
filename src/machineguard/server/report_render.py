"""从不可变正式报告生成可核对的 UTF-8 文本和中文 PDF。"""
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from xml.sax.saxutils import escape
from .auth_service import ServiceError
from .agent_service import jsonable

MIME = {"md": "text/markdown; charset=utf-8", "json": "application/json; charset=utf-8", "pdf": "application/pdf"}


def export_document(report):
    return jsonable({"schema": "machineguard.report-export.v1", "report_id": str(report.id),
        "run_id": str(report.run_id), "created_at": report.created_at, "content_sha256": report.content_sha256,
        "usage": "NASA C-MAPSS 模拟设备观测；剩余周期不是小时，风险等级不是故障概率。不得据此自动执行维护。",
        "content": report.content})


def readable_sections(document):
    content = document["content"]; prediction = content["prediction"]; trend = content["trend"]
    rows = [("设备综合分析报告", [f"报告编号：{document['report_id']}", f"保存时间：{document['created_at']}",
        document["usage"], f"设备：{trend['engine']['engine_key']}；末端周期：{trend['end_cycle']}",
        f"业务 RUL：{prediction['business_rul']} 运行周期；原始 RUL：{prediction['raw_rul']}",
        f"风险等级：{prediction['risk_level']}；模型版本：{prediction['model_version']}"]),
        ("人工审核", [f"审核人：{content['review']['reviewer_name']}", f"审核时间：{content['review']['created_at']}", content['review']['comment']])]
    claims = []
    for section in content["sections"]:
        claims.append(section["text"])
        for citation in section.get("citations", []):
            claims.append(f"引用：{citation['source_metadata']['title']}；版本：{citation['document_version']}；原句：{citation['quote']}")
    rows.append(("结论与引用", claims))
    rows.append(("使用限制与证据缺口", [*content.get("limitations", []), *[str(item) for item in content.get("evidence_gaps", [])]]))
    return rows


def render_report(document, format, config):
    if config["renderer_version"] != "report-delivery-v1":
        raise ServiceError(503, "EXPORT_RENDERER_CHANGED", "导出渲染版本不匹配。")
    complete = json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    if format == "json":
        return (complete + "\n").encode("utf-8")
    sections = readable_sections(document)
    if format == "md":
        lines = []
        for title, paragraphs in sections:
            lines.extend([f"## {title}", "", *[str(item) + "\n" for item in paragraphs], ""])
        # 完整字段附录保留模型哈希、预测窗口、数值精度与原始引用版本。
        lines.extend(["## 完整报告字段", "", "```json", complete, "```", ""])
        return "\n".join(lines).encode("utf-8")
    import reportlab
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
    from reportlab.pdfgen.canvas import Canvas
    try:
        if reportlab.Version != config["reportlab_version"] or sha256(Path(config["pdf_font_path"]).read_bytes()).hexdigest() != config["font_sha256"]:
            raise ValueError()
        font = "MGChinese-" + config["font_sha256"][:12]
        if font not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(font, config["pdf_font_path"], subfontIndex=0))
    except (OSError, ValueError, KeyError):
        raise ServiceError(503, "EXPORT_FONT_CHANGED", "固定中文字体或渲染版本校验失败。") from None
    body = ParagraphStyle("body", fontName=font, fontSize=10, leading=16, wordWrap="CJK", spaceAfter=7)
    heading = ParagraphStyle("heading", parent=body, fontSize=16, leading=23, textColor=HexColor("#24503c"), spaceBefore=10)
    appendix = ParagraphStyle("appendix", parent=body, fontSize=8, leading=12, spaceAfter=2)
    output = BytesIO(); story = []
    def paragraph(value, style):
        return Paragraph(escape(str(value)).replace("\n", "<br/>"), style)
    for title, paragraphs in sections:
        story.extend([paragraph(title, heading), Spacer(1, 7)])
        for value in paragraphs:
            story.append(paragraph(value, body))
    story.extend([PageBreak(), paragraph("附录：完整报告字段", heading)])
    # 按原 JSON 行分段，长引用可跨页，避免一个巨大段落超出页面高度。
    for line in complete.splitlines():
        story.append(paragraph(line, appendix))
    def footer(canvas, doc):
        canvas.setFont(font, 8); canvas.setFillColor(HexColor("#65796d"))
        canvas.drawString(42, 25, "MachineGuard / 模拟设备分析 / 人工审核通过")
        canvas.drawRightString(A4[0] - 42, 25, f"第 {doc.page} 页")
    def fixed_canvas(*args, **kwargs):
        kwargs["invariant"] = 1
        return Canvas(*args, **kwargs)
    SimpleDocTemplate(output, pagesize=A4, rightMargin=42, leftMargin=42, topMargin=35, bottomMargin=42,
                      title="MachineGuard 设备综合分析报告", author="MachineGuard").build(
                          story, onFirstPage=footer, onLaterPages=footer, canvasmaker=fixed_canvas)
    return output.getvalue()
