// 仅分流明确的图谱路径请求；普通资料解释仍使用原会话流程。
export function isEvidencePathQuestion(question: string): boolean {
  return /(?:知识图谱|图谱|证据路径)/.test(question)
    && /(?:路径|[一二两三\d]+跳)/.test(question)
    && /(?:查询|查找|查看|列出|找出|有哪些|从.+到)/.test(question)
}
