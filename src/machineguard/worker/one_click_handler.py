"""固定一键流程与报告候选；复用已有工具、租约、预算和来源核对。"""

import json
from uuid import uuid4
from sqlalchemy import select
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from machineguard.server import agent_service as service
from machineguard.server.agent_models import AgentRun
from machineguard.server.auth_service import ServiceError
from machineguard.server.business_config import load_business_configuration
from machineguard.server.device_service import DeviceRuntime
from machineguard.server.report_contract import (KNOWLEDGE_QUESTION, REPORT_PROMPT, FOCUS,
    report_evidence, assemble_report, validate_presentation)
from machineguard.server.skill_loader import SkillRegistry
from machineguard.server.tool_runtime import ToolRunner, permitted_tools
from .agent_handler import AnalysisExecution, GraphState
from .checkpoint_store import PostgresCheckpointSaver, ActionJournal, RECOVERY_VERSION


class OneClickExecution(AnalysisExecution):
    def __init__(self, context, payload):
        super().__init__(context, payload)
        with self.database.sessions() as session:
            version = session.get(AgentRun, self.run_id).recovery_version
            if version not in (None, RECOVERY_VERSION):
                self.cloud_client.close()
                raise ServiceError(409, "AGENT_RECOVERY_VERSION", "当前 Worker 不支持此运行冻结的恢复流程版本。")
            self.recoverable = version == RECOVERY_VERSION
        self.journal = ActionJournal(self)

    def prepare(self, state, restoring=False):
        with self.database.sessions() as session:
            row = session.get(AgentRun, self.run_id)
            self.options = dict(row.workflow_options)
        required = ["get_engine_overview", "get_sensor_trend", "predict_rul"]
        if self.options["include_knowledge"]:
            required.append("search_maintenance_knowledge")
        metadata = next(item for item in self.catalog if item["name"] == "engine-analysis")
        self.report_skill = next(item for item in self.catalog if item["name"] == "report-generation")
        if metadata["status"] != "available" or not self.report_skill.get("candidate_available"):
            raise ServiceError(403, "REPORT_SKILL_FORBIDDEN", "当前身份没有一键分析与报告候选权限。")
        registry = SkillRegistry()
        skill = registry.load(metadata["name"], metadata["version"])
        report_skill = registry.load(self.report_skill["name"], self.report_skill["version"])
        if skill.sha256 != metadata["sha256"] or report_skill.sha256 != self.report_skill["sha256"]:
            raise ServiceError(409, "AGENT_SKILL_CHANGED", "一键流程说明与提交时冻结版本不一致。")
        self.report_body = report_skill.body
        frozen = dict(self.scope)
        if not self.options["include_knowledge"]:
            frozen.update(snapshot_id=None, snapshot_manifest=None)
        with self.database.sessions.begin() as session:
            self.guard(session, True)
            row = session.scalar(select(AgentRun).where(AgentRun.id == self.run_id).with_for_update())
            principal = service.principal_for(session, row)
            if any(name not in permitted_tools(principal, skill.name) for name in required):
                raise ServiceError(403, "AGENT_PERMISSION_CHANGED", "一键分析所需工具授权已变化。")
            if restoring:
                if row.selected_skill != metadata or row.tool_scope != frozen:
                    raise ServiceError(409, "AGENT_SCOPE_CHANGED", "恢复时固定 Skill 或工具范围不一致。")
            else:
                row.selected_skill = metadata; row.tool_scope = frozen
            scope = service.restore_scope(row)
        self.report_scope = frozen
        self.runner = ToolRunner.from_frozen(self.database, DeviceRuntime(load_business_configuration()),
            skill, scope, required, self.guard, self.context.interrupted, self.cloud_client)
        if not restoring:
            self.event("workflow_selected", {"workflow": "fixed_one_click", "required_tools": required,
                "options": self.options, "report_skill": self.report_skill})
        return {"selection": {"status": "ready", "required_tools": required,
            "knowledge_question": KNOWLEDGE_QUESTION if self.options["include_knowledge"] else None},
            "observations": [], "messages": []}

    def ensure_runtime(self):
        if self.runner is None:
            self.prepare({}, restoring=True)

    def model(self, messages, tools=None):
        if not self.recoverable:
            return super().model(messages, tools)
        return self.journal.call("report_layout", {"messages": messages},
            lambda: super(OneClickExecution, self).model(messages, tools))

    def knowledge_model(self, stage, prompt, inputs, label, validator=None):
        if not self.recoverable:
            return super().knowledge_model(stage, prompt, inputs, label, validator)
        return self.journal.call(stage, {"prompt": prompt, "inputs": inputs},
            lambda: super(OneClickExecution, self).knowledge_model(stage, prompt, inputs, label, validator))

    def execute_tool(self, state, name):
        self.ensure_runtime()
        if name not in state["selection"]["required_tools"]:
            return {}
        arguments = {"get_engine_overview": {}, "get_sensor_trend": {
            "start_cycle": self.options["start_cycle"], "columns": self.options["columns"]},
            "predict_rul": {}, "search_maintenance_knowledge": {"query": KNOWLEDGE_QUESTION, "limit": 5}}[name]
        key = "tool_" + name
        observation = self.journal.begin(key, {"arguments": arguments, "scope": self.report_scope})
        if observation is None:
            self.event("tool_started", {"tool": name, "arguments": arguments, "origin": "fixed_workflow"}, "tool_calls")
            def complete(session, value, receipt):
                self.journal.complete(session, key, value)
                service.add_event(session, session.get(AgentRun, self.run_id), "tool_completed",
                    {"tool": name, "receipt": receipt, "observation": value})
            try:
                observation = self.runner.execute(name, arguments, uuid4(), completion=complete)
            except ServiceError as error:
                self.event("tool_failed", {"tool": name, "code": error.code}); raise
        return {"observations": [*state["observations"], observation]}

    def knowledge(self, state):
        self.ensure_runtime()
        return self.finalize(state)

    def execute_fixed(self, state):
        arguments = {"get_engine_overview": {}, "get_sensor_trend": {
            "start_cycle": self.options["start_cycle"], "columns": self.options["columns"]},
            "predict_rul": {}, "search_maintenance_knowledge": {"query": KNOWLEDGE_QUESTION, "limit": 5}}
        # 工具顺序由代码确定，不调用模型进行规划，也不伪装成模型 tool_calls。
        observations = []
        for name in state["selection"]["required_tools"]:
            self.event("tool_started", {"tool": name, "arguments": arguments[name], "origin": "fixed_workflow"}, "tool_calls")
            try:
                observation = self.runner.execute(name, arguments[name], uuid4())
            except ServiceError as error:
                self.event("tool_failed", {"tool": name, "code": error.code}); raise
            self.event("tool_completed", {"tool": name, "receipt": self.runner.trace[-1], "observation": observation})
            observations.append(observation)
        return {"observations": observations}

    def report(self, state):
        # 原有知识生成与逐条支持核对保持原实现，之后才装配报告候选。
        self.ensure_runtime()
        result = dict(state["result"]) if self.recoverable else self.finalize(state)["result"]
        evidence = report_evidence(result)
        presentation = self.model([{"role": "system", "content": REPORT_PROMPT}, {"role": "user", "content": json.dumps({
            "question": self.question, "skill_body": self.report_body,
            "evidence": [{key: item[key] for key in ("id", "kind", "text")} for item in evidence],
            "focus": FOCUS}, ensure_ascii=False)}])
        presentation = validate_presentation(presentation, evidence)
        result["report_candidate"] = assemble_report(self.run_id, self.report_scope, result, presentation, self.report_skill)
        self.event("report_candidate_created", {"candidate_id": str(self.run_id), "status": "unreviewed",
            "sections": len(result["report_candidate"]["sections"]), "skill": result["report_candidate"]["skill"]})
        self.context.stage("report_validating", 95)
        return {"result": result}

    def await_review(self, state):
        # 节点恢复时会重新执行中断之前的代码；此处没有审核或报告写入副作用。
        review_id = interrupt({"run_id": str(self.run_id), "candidate_id": str(self.run_id), "status": "pending"})
        from machineguard.server.report_service import checked_resume
        with self.database.sessions() as session:
            decision = checked_resume(session, session.get(AgentRun, self.run_id), state["result"], review_id)
            return {"review": {"id": str(decision.id), "decision": decision.decision}}

    def finalize_report(self, state):
        # 报告写入在 finish_agent 的终态事务执行，避免报告已存在而任务仍待完成的窗口。
        from machineguard.server.report_service import checked_resume
        self.context.stage("report_saving", 98)
        with self.database.sessions() as session:
            self.guard(session)
            checked_resume(session, session.get(AgentRun, self.run_id), state["result"], state["review"]["id"])
        return {}


def one_click_run(context, payload):
    execution = OneClickExecution(context, payload)
    graph = StateGraph(GraphState)
    graph.add_node("freeze_workflow", execution.prepare)
    if execution.recoverable:
        names = ["get_engine_overview", "get_sensor_trend", "predict_rul", "search_maintenance_knowledge"]
        previous = "freeze_workflow"
        for name in names:
            graph.add_node(name, lambda state, tool=name: execution.execute_tool(state, tool))
            graph.add_edge(previous, name); previous = name
        graph.add_node("knowledge_validation", execution.knowledge)
        graph.add_edge(previous, "knowledge_validation")
        graph.add_edge("knowledge_validation", "report_candidate")
        graph.add_node("await_review", execution.await_review)
        graph.add_node("finalize_report", execution.finalize_report)
        graph.add_edge("report_candidate", "await_review"); graph.add_edge("await_review", "finalize_report")
        graph.add_edge("finalize_report", END)
    else:
        graph.add_node("fixed_tools", execution.execute_fixed)
        graph.add_edge("freeze_workflow", "fixed_tools")
        graph.add_edge("fixed_tools", "report_candidate"); graph.add_edge("report_candidate", END)
    graph.add_node("report_candidate", execution.report)
    graph.add_edge(START, "freeze_workflow")
    try:
        if not execution.recoverable:
            return graph.compile().invoke({}, {"recursion_limit": 10})["result"]
        saver = PostgresCheckpointSaver(execution)
        config = {"configurable": {"thread_id": str(execution.run_id)}, "recursion_limit": 20}
        saved = saver.get_tuple(config)
        if context.lease.number > 1:
            execution.event("checkpoint_recovered", {"checkpoint_id": saved.checkpoint["id"] if saved else None,
                "attempt": context.lease.number})
        compiled = graph.compile(checkpointer=saver)
        from machineguard.server.report_models import ReportReview
        with execution.database.sessions() as session:
            decision = session.get(ReportReview, execution.run_id)
            review_id = str(decision.id) if decision else None
        inputs = None if saved else {}
        if review_id:
            snapshot = compiled.get_state(config)
            if saved is None or any(node not in ("await_review", "finalize_report") for node in snapshot.next):
                raise ServiceError(409, "REVIEW_RESUME_INVALID", "人工审核只能恢复审核之后的节点，不能重跑工具或模型。")
            if any(task.interrupts for task in snapshot.tasks):
                inputs = Command(resume=review_id)
        value = compiled.invoke(inputs, config, durability="sync")
        if review_id:
            if value.get("__interrupt__") or value.get("review", {}).get("id") != review_id:
                raise ServiceError(409, "REVIEW_RESUME_INVALID", "恢复图没有完成对应的人工决定。")
            return {"_review_completed": review_id, "result": value["result"]}
        if not value.get("__interrupt__"):
            raise ServiceError(409, "AGENT_CHECKPOINT_INVALID", "一键恢复流程没有到达待审核暂停节点。")
        return {"_waiting_review": True, "result": value["result"]}
    finally:
        execution.cloud_client.close()
