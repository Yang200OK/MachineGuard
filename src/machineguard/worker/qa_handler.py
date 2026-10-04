"""单轮召回、重排、证据门槛、引用回答和逐条支持核对。"""

import json
from hashlib import sha256
from types import SimpleNamespace
from uuid import UUID
from machineguard.server.auth_service import ServiceError
from machineguard.server.index_service import search_preview
from machineguard.server.job_service import heartbeat, JobInterrupted
from machineguard.server.qa_config import QAConfig
from machineguard.server.qa_contract import ANSWER_PROMPT, VERIFY_PROMPT, validate_answer, validate_support
from machineguard.server.qa_models import KnowledgeAnswer
from machineguard.server.qa_provider import generate_json, rerank
from machineguard.server.qa_service import assert_evidence_current, principal_for
from machineguard.server.conversation_config import ConversationConfig, load_conversation_configuration
from machineguard.server.conversation_service import validate_context
from machineguard.server.rewrite_contract import CONVERSATION_SUPPORT_PROMPT, TERM_PROMPT, assemble_followup, followup_references, sources, validate_terms
from machineguard.server.qa_followup import SUGGEST_PROMPT, asks_prediction, validate_suggestions


def knowledge_answer(context, payload):
    answer_id = UUID(payload["answer_id"])
    trace = {"usage": [], "retrieval": [], "reranked": [], "evidence": []}

    def save_trace():
        with context.database.sessions.begin() as session:
            if not heartbeat(session, context.lease, context.config):
                raise JobInterrupted()
            row = session.get(KnowledgeAnswer, answer_id)
            row.trace = json.loads(json.dumps(trace, ensure_ascii=False, allow_nan=False))

    def check(evidence):
        with context.database.sessions() as session:
            row = session.get(KnowledgeAnswer, answer_id)
            validate_context(session, principal_for(session, row), row)
            assert_evidence_current(session, row, evidence)

    clarification = []

    def refused(reason, evidence, guidance=None):
        context.stage("qa_save", 95)
        return {"status": "refused", "claims": [], "reason": reason, "evidence": evidence, "support_check": None,
                "clarification_questions": clarification, "suggestions": [], "suggestion_error": None, "guidance": guidance,
                "limitation": "指代不明确或当前固定快照没有足够证据回答；未调用回答生成模型。" if frozen_context else
                              "当前固定快照没有足够证据回答；未调用回答生成模型。"}

    with context.database.sessions() as session:
        row = session.get(KnowledgeAnswer, answer_id)
        if row is None or row.job_id != context.lease.job_id or row.result is not None:
            raise ServiceError(409, "QA_TARGET_INVALID", "问答目标与任务不一致。")
        config = QAConfig.model_validate(row.config_snapshot)
        if config.frozen()[1] != row.config_sha256:
            raise ServiceError(409, "QA_CONFIG_CHANGED", "问答配置指纹不一致。")
        question = row.question
        frozen_context = row.context_snapshot
        validate_context(session, principal_for(session, row), row)
    resolved_question = question
    if frozen_context is not None:
        context.stage("qa_rewrite", 5); check([])
        conversation_config = ConversationConfig.model_validate(frozen_context["config"])
        refs, fixed, reason = followup_references(question, frozen_context)
        anchor = (frozen_context["history"][-1].get("reference_topic") or frozen_context["history"][-1].get("resolved_question")) if frozen_context["history"] else None
        history_refs = [ref for ref in refs if not any(ref["start"] == item["start"] for item in fixed)]
        trace["rewrite"] = {"original_question": question, "status": "resolving" if refs else "unchanged", "resolved_question": question,
            "replacements": [], "history_turns": [turn["turn_number"] for turn in frozen_context["history"]],
            "contract_version": "restricted-reference-terms-v2",
            "prompt_sha256": sha256(TERM_PROMPT.encode("utf-8")).hexdigest()}
        save_trace()
        if any(ref.get("ellipsis") for ref in refs) and frozen_context.get("latest_refusal"):
            previous = frozen_context["latest_refusal"]
            trace["rewrite"]["status"] = "ambiguous"
            trace["rewrite"]["refusal_context"] = previous
            if previous.get("guidance") == "device_workbench":
                clarification = ["知识助手为什么不能给出具体设备的剩余周期？", "如何在设备工作台查看预测记录？"]
            save_trace()
            return refused("上一轮未回答的原因：" + previous["reason"] + " 如需继续，请写明想了解的内容。", [])
        if reason is None and history_refs:
            # 原问题可能仍含“它”；只从服务器提供的明确最近主题选择，避免选对词却关联错历史编号。
            history_sources = [{"source_id": "topic", "text": anchor}] if anchor else []
            trace["rewrite"]["reference_topic"] = anchor
            if not history_sources or not anchor:
                reason = "没有可用于解析此指代的有效历史，请在问题中写明对象。"
            else:
                raw, usage = generate_json(conversation_config, TERM_PROMPT, json.dumps(
                    {"question": question, "references": history_refs, "sources": history_sources,
                     "reference_topic": anchor}, ensure_ascii=False), "指代解析")
                trace["usage"].append(usage)
                trace["rewrite"]["candidate"] = raw
                save_trace()
                selected, options = validate_terms(raw, history_refs, history_sources, anchor)
                if selected is None:
                    clarification = [assemble_followup(question, fixed + option, anchor) for option in options]
                    reason = "你想问的是哪一个？请确认下面的问题，或在输入框中写明对象。" if clarification else "历史不足以唯一确定指代，请在输入框中写明对象。"
                else:
                    fixed.extend(selected)
        if reason:
            trace["rewrite"]["status"] = "ambiguous"; save_trace()
            if asks_prediction(question):
                return refused("具体设备的剩余周期需要读取可信预测记录。请到设备工作台选择设备和观测周期查看。", [], "device_workbench")
            return refused(reason, [])
        resolved_question = assemble_followup(question, fixed, anchor)
        trace["rewrite"].update({"status": "resolved" if fixed else "unchanged",
                                 "resolved_question": resolved_question, "replacements": fixed,
                                 "topic_question": anchor if any(item.get("ellipsis") for item in fixed) else resolved_question})
        save_trace(); check([])
    # 资料问答不读取设备预测；明确索要数值时给出业务入口，不浪费检索与生成调用。
    if asks_prediction(resolved_question):
        return refused("具体设备的剩余周期需要读取可信预测记录。请到设备工作台选择设备和观测周期查看。", [], "device_workbench")
    context.stage("qa_retrieve", 10)
    with context.database.sessions() as session:
        row = session.get(KnowledgeAnswer, answer_id)
        recalled = search_preview(session, principal_for(session, row), SimpleNamespace(
            query=resolved_question, snapshot_id=row.snapshot_id, equipment_type=row.equipment_type, limit=config.recall_limit))
    items = recalled["items"]
    trace["retrieval"] = items
    if recalled["query_usage"] is not None:
        trace["usage"].append({"stage": "embedding", **recalled["query_usage"]})
    save_trace()

    if not items:
        return refused(recalled.get("reason", "没有有效知识来源。"), [])
    context.stage("qa_rerank", 35); check(items)
    ranked, usage = rerank(config, resolved_question, [item["text"] for item in items])
    trace["usage"].append(usage)
    reranked = [{**items[x["index"]], "rerank_rank": rank, "rerank_score": x["relevance_score"]}
                for rank, x in enumerate(ranked, 1)]
    trace["reranked"] = reranked
    evidence = [{**item, "evidence_id": f"E{index}"} for index, item in enumerate(
        [item for item in reranked if item["rerank_score"] >= config.min_rerank_score][:config.evidence_limit], 1)]
    trace["evidence"] = evidence; save_trace()
    if not evidence:
        return refused("当前候选未达到已配置的重排证据门槛，缺少可用于回答的资料。", [])
    context.stage("qa_generate", 60); check(evidence)
    raw, usage = generate_json(config, ANSWER_PROMPT, json.dumps({"question": resolved_question,
        "evidence": [{"evidence_id": x["evidence_id"], "text": x["text"]} for x in evidence]}, ensure_ascii=False), "回答生成")
    trace["usage"].append(usage)
    trace["answer_candidate"] = raw
    trace["answer_prompt_sha256"] = sha256(ANSWER_PROMPT.encode("utf-8")).hexdigest()
    save_trace()
    answer = validate_answer(raw, evidence)
    if answer["status"] == "refused":
        # 拒答理由由代码限定，避免模型把没有依据的断言放入 reason 绕过引用校验。
        answer["reason"] = "已检索资料不足以支持对原问题的完整回答，请补充适用资料。"
    support = None
    if answer["status"] == "answered":
        context.stage("qa_verify", 80); check(evidence)
        verify_prompt = VERIFY_PROMPT
        verify_input = {"question": resolved_question, "claims": [{"text": c["text"], "quotes": [r["quote"] for r in c["citations"]]}
                        for c in answer["claims"]]}
        if frozen_context is not None:
            # 会话同时核对原意，历史只能解释指代，不能替代本轮引用来支持结论。
            verify_prompt += CONVERSATION_SUPPORT_PROMPT
            verify_input.update({"original_question": question, "reference_context": {
                "history": sources(frozen_context), "device": frozen_context["device"]},
                "replacements": trace["rewrite"]["replacements"]})
            trace["intent_check"] = {"prompt_sha256": sha256(verify_prompt.encode("utf-8")).hexdigest(),
                                     "original_question": question, "resolved_question": resolved_question}
            save_trace()
        raw_support, usage = generate_json(config, verify_prompt, json.dumps(verify_input, ensure_ascii=False), "结论支持核对")
        trace["usage"].append(usage); trace["support_candidate"] = raw_support; save_trace()
        support = validate_support(raw_support, len(answer["claims"]))
    suggestions = []; suggestion_error = None
    if answer["status"] == "answered":
        context.stage("qa_suggest", 90); check(evidence)
        auxiliary = ConversationConfig.model_validate(frozen_context["config"]) if frozen_context else load_conversation_configuration()
        trace["suggestions"] = {"status": "generating", "config_snapshot": auxiliary.frozen()[0],
                                "prompt_sha256": sha256(SUGGEST_PROMPT.encode("utf-8")).hexdigest()}
        save_trace()
        try:
            raw_suggestions, usage = generate_json(auxiliary, SUGGEST_PROMPT, json.dumps({"question": resolved_question,
                "claims": answer["claims"], "evidence": [{"evidence_id": item["evidence_id"], "text": item["text"],
                "section": item["section"], "title": item["source_metadata"]["title"]} for item in evidence]}, ensure_ascii=False), "追问建议")
            trace["usage"].append(usage); trace["suggestions"]["candidate"] = raw_suggestions; save_trace()
            suggestions = validate_suggestions(raw_suggestions, evidence, resolved_question)
            trace["suggestions"]["status"] = "completed"
        except ServiceError as failure:
            # 建议是独立的可选输出；失败明确保存，已经核对的回答不会被改写或替换。
            suggestion_error = {"code": failure.code, "message": failure.message}
            trace["suggestions"].update({"status": "failed", "error": suggestion_error})
        save_trace()
    context.stage("qa_save", 95); check(evidence)
    return {**answer, "evidence": evidence, "support_check": support,
        "clarification_questions": [], "suggestions": suggestions, "suggestion_error": suggestion_error,
        "guidance": None,
        "limitation": "引用身份和逐字原句由代码核对；结论支持另由模型核对，仍需人工查看原文，不能当作事实正确性证明。"}
