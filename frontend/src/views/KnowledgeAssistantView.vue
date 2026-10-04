<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElInput } from 'element-plus'
import { api } from '../api'
import { useAuthStore } from '../stores/auth'
import { cancelled, errorText, timestamp } from '../format'
import type { KnowledgeAnswer, KnowledgeSnapshot, QuestionRequest } from '../types'
import ErrorPanel from '../components/ErrorPanel.vue'

const auth = useAuthStore(); const route = useRoute(); const router = useRouter()
const allowed = computed(() => auth.user?.permissions.includes('knowledge:manage'))
const question = ref(''); const equipment = ref(''); const snapshot = ref('')
const snapshots = ref<KnowledgeSnapshot[]>([]); const history = ref<KnowledgeAnswer[]>([])
const selected = ref<KnowledgeAnswer | null>(null); const pending = ref<QuestionRequest | null>(null)
const page = ref(1); const total = ref(0); const busy = ref(false); const loading = ref(false); const error = ref(''); const notice = ref('')
const active = computed(() => selected.value && ['queued', 'running'].includes(selected.value.job_status))
const key = `machineguard.qa.${auth.user?.id}`
const stages: Record<string, string> = { queued: '等待 Worker', claimed: '已领取', qa_rewrite: '解析会话指代', qa_retrieve: '召回资料', qa_rerank: '重排证据', qa_generate: '生成回答候选', qa_verify: '核对结论支持', qa_save: '保存结果', completed: '已完成' }
const status: Record<string, string> = { queued: '排队', running: '执行中', succeeded: '已完成', failed: '失败', cancelled: '已取消' }
const sourceKinds = { original: '原始来源', project_summary: '项目概括', simulated_sop: '模拟 SOP' }
let timer: number | undefined; let disposed = false; let controller: AbortController | undefined
try {
  const value = sessionStorage.getItem(key)
  if (value) {
    const parsed = JSON.parse(value) as QuestionRequest
    if (typeof parsed.query !== 'string' || parsed.query.length > 1000 || typeof parsed.idempotency_key !== 'string') throw new Error()
    pending.value = parsed; question.value = parsed.query; snapshot.value = parsed.snapshot_id ?? ''; equipment.value = parsed.equipment_type ?? ''
  }
} catch { sessionStorage.removeItem(key) }

function stop() { window.clearTimeout(timer); timer = undefined }
function schedule() {
  stop()
  if (!disposed && active.value) timer = window.setTimeout(() => { void refresh() }, 2000)
}
async function loadHistory() {
  const result = await api.knowledgeAnswers(auth.token, (page.value - 1) * 10)
  if (disposed) return
  history.value = result.items; total.value = result.total
}
async function refresh() {
  if (!selected.value || loading.value) return
  stop(); controller?.abort(); controller = new AbortController(); loading.value = true; error.value = ''
  try {
    const value = await api.knowledgeAnswer(auth.token, selected.value.answer_id, controller.signal)
    if (disposed) return
    selected.value = value
    if (!active.value) await loadHistory()
    schedule()
  } catch (failure) { if (!cancelled(failure) && !disposed) error.value = errorText(failure) }
  finally { loading.value = false }
}
async function show(row: KnowledgeAnswer) {
  stop(); selected.value = row; notice.value = ''; error.value = ''
  await router.replace({ path: '/assistant/history', query: { answer_id: row.answer_id } })
  await refresh()
}
async function submit(original?: QuestionRequest) {
  if (busy.value || !allowed.value) return
  busy.value = true; error.value = ''; notice.value = ''; stop()
  const body = original ?? { query: question.value.trim(), snapshot_id: snapshot.value || null, equipment_type: equipment.value.trim() || null, idempotency_key: crypto.randomUUID() }
  pending.value = body; sessionStorage.setItem(key, JSON.stringify(body))
  try {
    const row = await api.askKnowledge(auth.token, body)
    if (disposed) return
    notice.value = row.replayed ? '已确认原问答请求，未重新调用模型。' : '问答已提交，完成后显示经过核对的结果。'
    await show(row); await loadHistory()
    notice.value = row.replayed ? '已确认原问答请求，未重新调用模型。' : '问答已提交，完成后显示经过核对的结果。'
  } catch (failure) { if (!disposed) error.value = errorText(failure) }
  finally { busy.value = false; schedule() }
}
function newQuestion() {
  stop(); pending.value = null; selected.value = null; question.value = ''; error.value = ''; notice.value = ''
  sessionStorage.removeItem(key); void router.replace('/assistant/history')
}
async function changePage() {
  error.value = ''
  try { await loadHistory() } catch (failure) { error.value = errorText(failure) }
}
async function cancel() {
  if (!selected.value || busy.value) return
  busy.value = true; error.value = ''
  try { await api.cancelJob(auth.token, selected.value.job_id); await refresh() }
  catch (failure) { error.value = errorText(failure) }
  finally { busy.value = false }
}
onMounted(async () => {
  if (!allowed.value) return
  loading.value = true
  try {
    const result = await api.knowledgeSnapshots(auth.token)
    snapshots.value = result.items.filter(row => row.published_at)
    await loadHistory()
    const id = route.query.answer_id
    if (typeof id === 'string') selected.value = await api.knowledgeAnswer(auth.token, id)
    schedule()
  } catch (failure) { error.value = errorText(failure) }
  finally { loading.value = false }
})
onBeforeUnmount(() => { disposed = true; stop(); controller?.abort() })
</script>

<template>
  <div class="page-heading"><div><div class="eyebrow">KNOWLEDGE ASSISTANT</div><h1>知识助手</h1><p>回答固定到已发布资料，逐条展示支持原句和出处。</p></div><router-link v-if="allowed" class="text-link" to="/assistant/conversations">多轮资料会话</router-link></div>
  <ErrorPanel :message="error" />
  <div v-if="!allowed" class="scope-note">当前资料限定内部使用，本轮知识问答仅开放给管理员。</div>
  <template v-else>
    <section class="panel question-panel">
      <p class="method-caption">本轮为单轮资料问答。问题和候选正文会发送到百炼并产生模型用量；无证据时拒答，接口或校验失败时明确显示。设备剩余寿命请在设备工作台查询。</p>
      <label>问题<el-input v-model="question" type="textarea" :rows="3" maxlength="1000" show-word-limit aria-label="知识问题" :disabled="!!pending || busy" /></label>
      <div class="fields"><label>知识快照<el-select v-model="snapshot" placeholder="提交时固定当前已发布快照" :disabled="!!pending || busy"><el-option label="提交时固定当前已发布快照" value="" /><el-option v-for="row in snapshots" :key="row.snapshot_id" :value="row.snapshot_id" :label="row.snapshot_id" /></el-select></label><label>设备类型（可留空）<el-input v-model="equipment" maxlength="200" aria-label="问答设备类型" :disabled="!!pending || busy" /></label></div>
      <div class="actions"><el-button type="primary" :loading="busy" :disabled="!question.trim() || !!pending" @click="submit()">提交资料问答</el-button><el-button v-if="pending" :disabled="busy" @click="submit(pending)">确认原问答请求</el-button><el-button :disabled="busy || !!active" @click="newQuestion">开始新问题</el-button></div>
      <p v-if="notice" role="status">{{ notice }}</p>
    </section>
    <section v-if="selected" class="panel answer-panel">
      <div class="panel-heading"><h2>{{ selected.result?.status === 'refused' ? '资料不足，已拒答' : selected.result?.status === 'answered' ? '资料回答' : status[selected.job_status] }}</h2><el-button :loading="loading" @click="refresh">刷新结果</el-button></div>
      <p>{{ selected.question }}</p><p class="method-caption">{{ stages[selected.stage] ?? selected.stage }} · {{ selected.progress }}%</p>
      <p class="mono">知识快照：{{ selected.snapshot_id }}</p>
      <div class="actions"><RouterLink :to="{ path: '/jobs', query: { job_id: selected.job_id } }">查看后台任务</RouterLink><RouterLink v-if="selected.conversation_id" :to="{ path: '/assistant/conversations', query: { conversation_id: selected.conversation_id } }">查看所属会话</RouterLink><el-button v-if="active" :disabled="busy" @click="cancel">取消问答</el-button></div>
      <ErrorPanel v-if="selected.error" :message="`${selected.error.message}（${selected.error.code}）`" />
      <template v-if="selected.result">
        <p v-if="selected.result.status === 'refused'" class="refusal">{{ selected.result.reason }}</p>
        <article v-for="(claim, index) in selected.result.claims" :key="index" class="claim">
          <p class="claim-text">{{ claim.text }}</p>
          <details v-for="(citation, i) in claim.citations" :key="`${citation.evidence_id}-${i}`" open>
            <summary>{{ citation.evidence_id }} · {{ citation.source_metadata.title }} · 版本 {{ citation.document_version }} · 页 {{ citation.page_spans.map(span => span.page).join('、') }}</summary>
            <blockquote>{{ citation.quote }}</blockquote>
            <p class="method-caption">{{ citation.section }} · 字符范围 {{ citation.char_start }}–{{ citation.char_end }} · {{ sourceKinds[citation.source_metadata.source_kind] }}</p>
            <RouterLink :to="{ path: '/knowledge', query: { document_id: citation.source_id, version_id: citation.version_id, answer_id: selected.answer_id, claim_index: String(index), citation_index: String(i) } }">在知识管理核对来源</RouterLink>
          </details>
        </article>
        <p class="scope-note">{{ selected.result.limitation }} 历史结果保留生成时的依据，来源后来停用或纠正不会改写旧记录。</p>
      </template>
      <details class="trace"><summary>重排证据与模型用量</summary>
        <div v-for="item in selected.trace.reranked" :key="item.chunk_id" class="rank-row"><p>重排名次 {{ item.rerank_rank }} · 分数 {{ item.rerank_score.toFixed(4) }} · 向量名次 {{ item.ranks.vector ?? '未命中' }} · 关键词名次 {{ item.ranks.bm25 ?? '未命中' }}</p><p>{{ item.source_metadata.title }} · 版本 {{ item.document_version }} · 页 {{ item.page }}</p></div>
        <p class="method-caption">分数用于本次排序；证据门槛为初始配置，不代表置信概率或已完成质量标定。</p>
        <pre>{{ JSON.stringify({ configuration: selected.config_snapshot, config_sha256: selected.config_sha256, usage: selected.trace.usage }, null, 2) }}</pre>
      </details>
    </section>
    <section class="panel history-panel"><div class="panel-heading"><h2>本人问答记录</h2><el-button @click="changePage">刷新记录</el-button></div>
      <div v-for="row in history" :key="row.answer_id" class="history-row"><button type="button" @click="show(row)">{{ row.question }}</button><span>{{ row.result?.status === 'refused' ? '拒答' : status[row.job_status] }} · {{ timestamp(row.created_at) }}</span></div>
      <p v-if="!history.length">尚无问答记录。</p><el-pagination v-if="total > 10" v-model:current-page="page" :page-size="10" :total="total" layout="prev, pager, next" @current-change="changePage" />
    </section>
  </template>
</template>

<style scoped>
.question-panel label{display:block;font-size:13px}.question-panel label :deep(.el-input),.question-panel label :deep(.el-select){margin-top:8px;width:100%}.fields{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin:20px 0}.actions{display:flex;align-items:center;flex-wrap:wrap;gap:12px}.answer-panel,.history-panel{margin-top:22px}.claim{margin:24px 0;border-top:1px solid var(--line);padding-top:12px}.claim-text{font-size:16px;line-height:1.9}.claim summary{cursor:pointer;font-size:13px}.claim blockquote{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6f8;margin:12px 0;padding:16px;border-left:3px solid #81ab9b;line-height:1.9}.refusal{padding:20px;background:#f7f4ec;line-height:1.9}.trace{margin-top:22px}.trace pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6f8;padding:16px;max-height:350px;overflow:auto;font-size:12px}.rank-row{font-size:12px;border-bottom:1px solid #e8eeea;padding:8px 0}.history-row{display:flex;align-items:center;justify-content:space-between;gap:20px;border-top:1px solid #e8eeea;padding:16px 0}.history-row button{border:0;background:none;text-align:left;font:inherit;color:#386f5b;cursor:pointer;overflow-wrap:anywhere}.history-row span{font-size:12px;color:#6d7d76;flex-shrink:0}@media(max-width:760px){.fields{grid-template-columns:1fr}.history-row{display:block}.history-row span{display:block;margin-top:8px}}
</style>
