<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElDrawer } from 'element-plus'
import type { KnowledgeAnswer } from '../types'
const props = defineProps<{ answer: KnowledgeAnswer; embeddingModel?: string }>()
const opened = ref(false)
// 按切片身份合并展示；完整多阶段记录仍保留在技术详情中。
const candidates = computed(() => {
  const map = new Map<string, { id: string; title: string; section: string; version: number; page: number; text: string; vector?: number; bm25?: number; rerank?: number; score?: number; evidence?: string }>()
  for (const item of props.answer.trace.retrieval ?? []) map.set(item.chunk_id, { id: item.chunk_id, title: item.source_metadata.title, section: item.section, version: item.document_version, page: item.page, text: item.text, vector: item.ranks.vector, bm25: item.ranks.bm25 })
  for (const item of props.answer.trace.reranked ?? []) {
    const old = map.get(item.chunk_id)
    if (old) { old.rerank = item.rerank_rank; old.score = item.rerank_score }
  }
  for (const item of props.answer.trace.evidence ?? []) { const old = map.get(item.chunk_id); if (old) old.evidence = item.evidence_id }
  return [...map.values()].sort((a, b) => (a.rerank ?? 999) - (b.rerank ?? 999))
})
interface UsageRow { stage?: string; model?: string; request_id?: string; usage?: Record<string, unknown> | null }
const uses = computed(() => (props.answer.trace.usage ?? []) as UsageRow[])
const rewriteLabels: Record<string, string> = { resolved: '已解析指代', unchanged: '无需改写', ambiguous: '无法确定指代', resolving: '解析中' }
const history = computed(() => (props.answer.context_snapshot?.history ?? []) as { turn_number: number; question: string; resolved_question?: string; quotes: { text: string }[] }[])
</script>
<template>
  <el-button class="processing-trigger" text @click="opened = true">处理详情</el-button>
  <el-drawer v-model="opened" title="回答处理详情" size="min(680px, 100vw)" append-to-body destroy-on-close>
  <div class="processing-details">
    <section><h3>问题理解</h3><p>原问题：{{ answer.question }}</p><template v-if="answer.trace.rewrite"><p>{{ rewriteLabels[answer.trace.rewrite.status] || answer.trace.rewrite.status }}</p><p v-if="['resolved', 'unchanged'].includes(answer.trace.rewrite.status)">检索问题：{{ answer.trace.rewrite.resolved_question }}</p><p v-else>尚未确定检索问题。</p><details v-if="answer.trace.rewrite.status === 'resolved'"><summary>查看指代依据</summary><p v-for="item in history" :key="item.turn_number">第 {{ item.turn_number }} 轮：{{ item.resolved_question || item.question }}</p><pre>{{ JSON.stringify(answer.trace.rewrite.replacements, null, 2) }}</pre></details></template><p v-else>本轮尚未保存问题解析记录。</p></section>
    <section><h3>检索与证据</h3><p v-if="!candidates.length">本轮没有已保存的检索候选。</p><details v-for="item in candidates" :key="item.id" class="candidate"><summary>{{ item.evidence ? `${item.evidence} · ` : '' }}{{ item.title }} · {{ item.section }} · 版本 {{ item.version }} · 页 {{ item.page }}</summary><p>向量 {{ item.vector ?? '未命中' }} · 关键词 {{ item.bm25 ?? '未命中' }} · 重排 {{ item.rerank ?? '未执行' }}<span v-if="item.score !== undefined"> · 分数 {{ item.score.toFixed(4) }}</span></p><p class="snippet">{{ Array.from(item.text).slice(0, 180).join('') }}{{ Array.from(item.text).length > 180 ? '…' : '' }}</p><details><summary>查看此切片完整正文</summary><pre>{{ item.text }}</pre></details></details><p class="muted">分数用于本次排序，不代表答案正确率。同一切片在各阶段仅合并展示，原记录保留。</p></section>
    <section><h3>模型用量</h3><p v-if="!uses.length">本轮没有已记录的模型调用。</p><div v-for="(use, index) in uses" :key="index" class="usage-row"><strong>{{ use.stage === 'embedding' ? '查询向量' : use.stage === 'rerank' ? '证据重排' : use.stage }}</strong><span>{{ use.model || (use.stage === 'embedding' ? embeddingModel : undefined) || '模型名称未记录' }}</span><span>输入 {{ use.usage?.input_tokens ?? use.usage?.prompt_tokens ?? '未记录' }} · 输出 {{ use.usage?.output_tokens ?? use.usage?.completion_tokens ?? '未记录' }} · 总计 {{ use.usage?.total_tokens ?? '未记录' }} Token</span><details><summary>调用编号与原始用量</summary><pre>{{ JSON.stringify(use, null, 2) }}</pre></details></div></section>
    <router-link class="text-link" :to="{ path: '/jobs', query: { job_id: answer.job_id } }">查看后台任务</router-link>
    <details class="technical"><summary>技术记录：完整配置与追溯 JSON</summary><pre>{{ JSON.stringify({ context: answer.context_snapshot, trace: answer.trace, configuration: answer.config_snapshot }, null, 2) }}</pre></details>
  </div></el-drawer>
</template>
<style scoped>
.processing-trigger{margin-top:12px;font-size:12px}.processing-details{color:#748777;font-size:12px;overflow-wrap:anywhere}.processing-details summary{cursor:pointer;line-height:1.7}.processing-details section{border-top:1px solid #e6ece3;padding:16px 0;margin-top:12px}.processing-details h3{font-size:13px;color:#4c6a51;margin-bottom:12px}.processing-details p{line-height:1.9;margin:8px 0}.candidate{padding:12px 0;border-bottom:1px solid #e9eee6}.snippet{background:#f3f6ef;padding:12px;border-radius:6px}.usage-row{padding:12px 0;border-bottom:1px solid #e9eee6;display:grid;gap:6px}.technical{margin-top:16px}.processing-details pre{white-space:pre-wrap;overflow:auto;overflow-wrap:anywhere;max-height:300px;background:#f4f6f1;padding:12px;font-size:11px}
</style>
