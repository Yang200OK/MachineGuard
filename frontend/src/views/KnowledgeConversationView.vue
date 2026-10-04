<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElInput, ElInputNumber } from 'element-plus'
import { api } from '../api'
import { useAuthStore } from '../stores/auth'
import { useSelectionStore } from '../stores/selection'
import { cancelled, errorText, timestamp } from '../format'
import type { ConversationDetail, ConversationRequest, KnowledgeConversation, KnowledgeSnapshot, QuestionRequest } from '../types'
import ErrorPanel from '../components/ErrorPanel.vue'

const auth = useAuthStore(); const route = useRoute(); const router = useRouter()
const selection = useSelectionStore()
const allowed = computed(() => auth.user?.permissions.includes('knowledge:manage'))
const title = ref('资料会话'); const snapshot = ref(''); const equipment = ref(''); const engine = ref(''); const cycle = ref<number | undefined>()
const question = ref(''); const error = ref(''); const notice = ref(''); const busy = ref(false); const loading = ref(false)
const page = ref(1); const total = ref(0); const conversations = ref<KnowledgeConversation[]>([]); const snapshots = ref<KnowledgeSnapshot[]>([])
const current = ref<ConversationDetail | null>(null); const pending = ref<QuestionRequest | null>(null)
const creation = ref<ConversationRequest | null>(null)
const invalidCache = ref(false)
const active = computed(() => current.value?.turns.some(turn => ['queued', 'running'].includes(turn.job_status)))
const prefix = `machineguard.conversation.${auth.user?.id}`; const createKey = `${prefix}.create`
const labels: Record<string, string> = { queued: '排队', running: '执行中', succeeded: '已完成', failed: '失败', cancelled: '已取消' }
const stages: Record<string, string> = { queued: '等待 Worker', claimed: '已领取', completed: '已完成', qa_rewrite: '解析指代', qa_retrieve: '召回资料', qa_rerank: '重排证据', qa_generate: '生成候选', qa_verify: '核对支持与原意', qa_save: '保存结果' }
let disposed = false; let timer: number | undefined; let controller: AbortController | undefined

function restoreCreation() {
  try {
    const saved = sessionStorage.getItem(createKey)
    if (!saved) return
    const value = JSON.parse(saved) as ConversationRequest
    if (typeof value.title !== 'string' || typeof value.idempotency_key !== 'string') throw new Error()
    creation.value = value; title.value = value.title; snapshot.value = value.snapshot_id ?? ''; equipment.value = value.equipment_type ?? ''
    engine.value = value.engine_key ?? ''; cycle.value = value.end_cycle ?? undefined
  } catch { invalidCache.value = true; error.value = '本地会话请求记录损坏，原结果尚未确认。请先在本人会话列表核对，再显式开始新会话。' }
}
function useDeviceSelection() {
  error.value = ''
  try {
    selection.restore()
    if (!selection.selected) { error.value = '请先在设备工作台选择设备和观测周期。'; return }
    engine.value = selection.selected.engineKey; cycle.value = selection.selected.endCycle
  } catch (failure) { error.value = errorText(failure) }
}
function restoreTurn(id: string) {
  pending.value = null; question.value = ''; invalidCache.value = false
  try {
    const saved = sessionStorage.getItem(`${prefix}.${id}`)
    if (!saved) return
    const value = JSON.parse(saved) as QuestionRequest
    if (value.conversation_id !== id || typeof value.query !== 'string' || typeof value.idempotency_key !== 'string') throw new Error()
    pending.value = value; question.value = value.query
  } catch { invalidCache.value = true; error.value = '本地轮次请求记录损坏。请先核对已保存的轮次，再点击继续下一轮。' }
}
function stop() { window.clearTimeout(timer); timer = undefined }
function schedule() {
  stop()
  if (!disposed && active.value) timer = window.setTimeout(() => { void refresh() }, 2000)
}
async function listing() {
  const value = await api.conversations(auth.token, (page.value - 1) * 10)
  if (!disposed) { conversations.value = value.items; total.value = value.total }
}
async function refresh() {
  if (!current.value || loading.value) return
  stop(); controller?.abort(); controller = new AbortController(); const signal = controller.signal
  const id = current.value.conversation_id; loading.value = true; error.value = ''
  try {
    const value = await api.conversation(auth.token, id, signal)
    if (!disposed && !signal.aborted && current.value?.conversation_id === id) current.value = value
    if (!active.value) await listing()
  } catch (failure) { if (!cancelled(failure) && !disposed) error.value = errorText(failure) }
  finally { loading.value = false; schedule() }
}
async function show(id: string) {
  stop(); controller?.abort(); controller = new AbortController(); const signal = controller.signal
  current.value = null; pending.value = null; error.value = ''; notice.value = ''; loading.value = true
  try {
    const value = await api.conversation(auth.token, id, signal)
    if (disposed || signal.aborted) return
    current.value = value; restoreTurn(id)
    await router.replace({ path: '/assistant/conversations', query: { conversation_id: id } })
  } catch (failure) { if (!cancelled(failure) && !disposed) error.value = errorText(failure) }
  finally { loading.value = false; schedule() }
}
async function create(original?: ConversationRequest) {
  if (busy.value) return
  busy.value = true; error.value = ''
  const body = original ?? { title: title.value.trim(), snapshot_id: snapshot.value || null, equipment_type: equipment.value.trim() || null,
    engine_key: engine.value.trim() || null, end_cycle: cycle.value ?? null, idempotency_key: crypto.randomUUID() }
  creation.value = body; sessionStorage.setItem(createKey, JSON.stringify(body))
  try {
    const value = await api.createConversation(auth.token, body)
    if (disposed) return
    await show(value.conversation_id); await listing()
    notice.value = value.replayed ? '已确认原会话，未重复创建。' : '会话已创建，资料与设备范围已固定。'
  } catch (failure) { if (!disposed) error.value = errorText(failure) }
  finally { busy.value = false }
}
async function submit(original?: QuestionRequest) {
  if (!current.value || busy.value) return
  busy.value = true; error.value = ''; notice.value = ''
  const id = current.value.conversation_id
  const body = original ?? { query: question.value.trim(), snapshot_id: null, equipment_type: null, conversation_id: id,
    expected_revision: current.value.revision, idempotency_key: crypto.randomUUID() }
  pending.value = body; sessionStorage.setItem(`${prefix}.${id}`, JSON.stringify(body))
  try {
    const answer = await api.askKnowledge(auth.token, body)
    if (disposed) return
    await refresh()
    notice.value = answer.replayed ? '已确认原轮次，未重新调用模型。' : '本轮已提交，完成后可继续提问。'
  } catch (failure) { if (!disposed) error.value = errorText(failure) }
  finally { busy.value = false; schedule() }
}
function nextQuestion() {
  if (!current.value || active.value) return
  sessionStorage.removeItem(`${prefix}.${current.value.conversation_id}`)
  pending.value = null; question.value = ''; invalidCache.value = false; error.value = ''; notice.value = ''
}
function newConversation() {
  stop(); controller?.abort(); current.value = null; creation.value = null; pending.value = null; question.value = ''
  sessionStorage.removeItem(createKey); invalidCache.value = false; error.value = ''; notice.value = ''; void router.replace('/assistant/conversations')
}
async function cancel() {
  const turn = current.value?.turns.find(item => ['queued', 'running'].includes(item.job_status))
  if (!turn || busy.value) return
  busy.value = true; error.value = ''
  try { await api.cancelJob(auth.token, turn.job_id); await refresh() }
  catch (failure) { error.value = errorText(failure) }
  finally { busy.value = false }
}
async function changePage() {
  try { await listing() } catch (failure) { error.value = errorText(failure) }
}
onMounted(async () => {
  if (!allowed.value) return
  restoreCreation()
  try {
    const value = await api.knowledgeSnapshots(auth.token)
    if (disposed) return
    snapshots.value = value.items.filter(item => item.published_at)
    await listing()
    if (typeof route.query.conversation_id === 'string') await show(route.query.conversation_id)
  } catch (failure) { if (!disposed) error.value = errorText(failure) }
})
onBeforeUnmount(() => { disposed = true; stop(); controller?.abort() })
</script>

<template>
  <div class="page-heading"><div><div class="eyebrow">KNOWLEDGE CONVERSATION</div><h1>多轮资料会话</h1><p>固定资料范围，保留原问题，按原文逐轮核对。</p></div><router-link class="text-link" to="/assistant">单轮问答与历史</router-link></div>
  <ErrorPanel :message="error" />
  <p v-if="!allowed" class="scope-note">当前内部资料会话仅开放给管理员。</p>
  <template v-else>
    <section v-if="!current" class="panel">
      <h2>创建会话</h2><p class="method-caption">会话创建后固定快照、设备类型及可选设备周期。问题和有限历史原词会发送到百炼；每轮最多执行一次。设备数值请在设备工作台查询。</p>
      <div class="fields"><label>会话名称<el-input v-model="title" aria-label="会话名称" maxlength="100" :disabled="!!creation || busy" /></label><label>知识快照<el-select v-model="snapshot" :disabled="!!creation || busy" placeholder="创建时固定当前已发布快照"><el-option label="创建时固定当前已发布快照" value="" /><el-option v-for="row in snapshots" :key="row.snapshot_id" :value="row.snapshot_id" :label="row.snapshot_id" /></el-select></label><label>设备类型（可留空）<el-input v-model="equipment" aria-label="会话设备类型" maxlength="200" :disabled="!!creation || busy" /></label><label>设备代码（可留空）<el-input v-model="engine" aria-label="会话设备代码" maxlength="96" :disabled="!!creation || busy" /></label><label>观测周期（与设备代码同时填写）<el-input-number v-model="cycle" aria-label="会话观测周期" :min="1" :precision="0" :disabled="!!creation || busy" /></label></div>
      <div class="actions"><el-button type="primary" :loading="busy" :disabled="!title.trim() || !!creation || invalidCache" @click="create()">创建会话</el-button><el-button :disabled="!!creation || busy" @click="useDeviceSelection">使用设备工作台当前选择</el-button><el-button v-if="creation" :disabled="busy" @click="create(creation)">确认原会话请求</el-button><el-button v-if="creation || invalidCache" :disabled="busy" @click="newConversation">开始新会话</el-button></div>
    </section>
    <section v-else class="panel">
      <div class="section-heading"><h2>{{ current.title }}</h2><el-button :disabled="busy || loading" @click="refresh">刷新会话</el-button></div>
      <p>知识快照：<span class="identifier">{{ current.snapshot_id }}</span></p><p>设备类型：{{ current.equipment_type || '未限定' }}；已提交 {{ current.revision }} / {{ current.config_snapshot.max_turns }} 轮</p>
      <p v-if="current.device_context">固定设备：{{ current.device_context.engine_key }}；观测周期：{{ current.device_context.end_cycle }}</p>
      <p class="method-caption">只解析明确指代，不补充异常或故障事实；含糊时拒答。每轮回答使用本轮检索的原文，历史结论不作为新证据。</p>
      <label>本轮问题<el-input v-model="question" aria-label="会话问题" type="textarea" :rows="3" maxlength="1000" show-word-limit :disabled="!!pending || busy || !!active" /></label>
      <div class="actions"><el-button type="primary" :loading="busy" :disabled="!!pending || !!active || invalidCache || !question.trim() || current.revision >= Number(current.config_snapshot.max_turns)" @click="submit()">提交本轮问题</el-button><el-button v-if="pending" :disabled="busy" @click="submit(pending)">确认原轮次请求</el-button><el-button :disabled="busy || !!active" @click="nextQuestion">继续下一轮</el-button><el-button v-if="active" :disabled="busy" @click="cancel">取消本轮</el-button><el-button :disabled="busy" @click="newConversation">开始新会话</el-button></div>
    </section>
    <p v-if="notice" class="scope-note" role="status">{{ notice }}</p>
    <section v-for="turn in current?.turns" :key="turn.answer_id" class="panel turn">
      <div class="section-heading"><h2>第 {{ turn.turn_number }} 轮</h2><router-link class="text-link" :to="{ path: '/jobs', query: { job_id: turn.job_id } }">查看后台任务</router-link></div>
      <p>{{ turn.question }}</p><p class="method-caption">{{ labels[turn.job_status] }} · {{ stages[turn.stage] || turn.stage }} · {{ turn.progress }}% · {{ timestamp(turn.created_at) }}</p>
      <p v-if="turn.trace.rewrite">指代解析：{{ ({ resolved: '已解析', unchanged: '无需改写', ambiguous: '无法确定', resolving: '未完成' } as Record<string, string>)[turn.trace.rewrite.status] || turn.trace.rewrite.status }}；{{ ['resolved', 'unchanged'].includes(turn.trace.rewrite.status) ? '检索问题：' : '未执行检索；原问题：' }}{{ turn.trace.rewrite.resolved_question }}</p>
      <div v-if="turn.error" class="scope-note">{{ turn.error.message }}（{{ turn.error.code }}）</div>
      <template v-if="turn.result">
        <div v-if="turn.result.status === 'refused'" class="scope-note">资料不足或指代不明确，已拒答：{{ turn.result.reason }}</div>
        <div v-for="(claim, index) in turn.result.claims" :key="index" class="claim"><p>{{ claim.text }}</p>
          <details v-for="(citation, i) in claim.citations" :key="i" open>
            <summary>{{ citation.evidence_id }} · {{ citation.source_metadata.title }} · 版本 {{ citation.document_version }} · 页 {{ citation.page_spans.map(span => span.page).join('、') }}</summary>
            <blockquote>{{ citation.quote }}</blockquote><p class="method-caption">{{ citation.section }} · 字符 {{ citation.char_start }}–{{ citation.char_end }}</p>
            <router-link class="text-link" :to="{ path: '/knowledge', query: { document_id: citation.source_id } }">在知识管理核对来源</router-link>
          </details>
        </div><p class="scope-note">{{ turn.result.limitation }} 历史记录保留生成时依据，来源后续变化不会改写历史。</p>
      </template>
      <details><summary>指代依据、重排与模型用量</summary><pre>{{ JSON.stringify({ context: turn.context_snapshot, trace: turn.trace, configuration: turn.config_snapshot }, null, 2) }}</pre></details>
    </section>
    <section class="panel"><div class="section-heading"><h2>本人会话</h2><el-button :disabled="busy" @click="changePage">刷新列表</el-button></div>
      <p v-if="!conversations.length" class="method-caption">尚无会话。</p>
      <button v-for="row in conversations" :key="row.conversation_id" class="history-row" :disabled="busy || loading" @click="show(row.conversation_id)"><span>{{ row.title }}</span><small>{{ row.revision }} 轮 · {{ timestamp(row.created_at) }}</small></button>
      <el-pagination v-if="total > 10" v-model:current-page="page" :page-size="10" :total="total" layout="prev, pager, next" @current-change="changePage" />
    </section>
  </template>
</template>

<style scoped>
.panel { margin-bottom: 20px; padding: 24px; }
.fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; margin: 20px 0; }
label { display: grid; gap: 8px; min-width: 0; }
.actions { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 18px; }
.actions :deep(.el-button) { margin-left: 0; }
.section-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.identifier, .turn { overflow-wrap: anywhere; }
.claim { margin: 20px 0; }
blockquote { white-space: pre-wrap; margin: 12px 0; padding: 16px; border-left: 3px solid var(--color-primary, #83aaa1); background: #f4f6f8; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 420px; overflow: auto; padding: 16px; background: #f4f6f8; font-size: 12px; }
.history-row { display: flex; gap: 12px; justify-content: space-between; text-align: left; width: 100%; padding: 16px 0; border: 0; border-bottom: 1px solid #edf0ee; background: transparent; color: inherit; cursor: pointer; }
.history-row small { color: #7b8982; }
@media (max-width: 600px) { .fields { grid-template-columns: 1fr; } .panel { padding: 16px; } .history-row { flex-direction: column; } }
</style>
