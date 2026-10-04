<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElDialog, ElDrawer, ElInput, ElInputNumber } from 'element-plus'
import { api, ApiError } from '../api'
import { useAuthStore } from '../stores/auth'
import { useSelectionStore } from '../stores/selection'
import { useConversationsStore } from '../stores/conversations'
import { cancelled, errorText, timestamp } from '../format'
import type { CitationReference, ConversationDetail, ConversationRequest, KnowledgeAnswer, KnowledgeSnapshot, QuestionRequest } from '../types'
import ErrorPanel from '../components/ErrorPanel.vue'
import CitationSource from '../components/CitationSource.vue'
import AnswerProcessingDetails from '../components/AnswerProcessingDetails.vue'
import { isEvidencePathQuestion } from '../assistantIntent'
import AppIcon from '../components/AppIcon.vue'
import ConversationHistory from '../components/ConversationHistory.vue'

const emit = defineEmits<{ navigationLock: [value: boolean] }>()
const auth = useAuthStore(); const route = useRoute(); const router = useRouter()
const selection = useSelectionStore(); const history = useConversationsStore()
const allowed = computed(() => auth.user?.permissions.includes('knowledge:manage'))
// 显式值用于显示默认选项；提交时仍用 null 表示创建时的当前发布资料。
const CURRENT_SNAPSHOT = 'current'
const snapshot = ref(CURRENT_SNAPSHOT); const equipment = ref(''); const engine = ref(''); const cycle = ref<number | undefined>()
const historyCollapsed = ref(localStorage.getItem('machineguard.chat-history.collapsed') === 'true')
const historyMobileOpen = ref(false)
const legacy = ref<KnowledgeAnswer | null>(null)
const displayTurns = computed(() => legacy.value ? [legacy.value] : current.value?.turns ?? [])
function toggleHistory() { historyCollapsed.value = !historyCollapsed.value; localStorage.setItem('machineguard.chat-history.collapsed', String(historyCollapsed.value)) }
async function focusTurn() { await nextTick(); if (typeof route.query.turn_id === 'string') document.getElementById(`turn-${route.query.turn_id}`)?.scrollIntoView({ block: 'center' }) }
async function showLegacy(id: string) {
  stop(); controller?.abort(); controller = new AbortController(); const signal = controller.signal; current.value = null; legacy.value = null; pending.value = null; creation.value = null; question.value = ''; error.value = ''; loading.value = true
  try { const row = await api.knowledgeAnswer(auth.token, id, signal); if (signal.aborted || disposed) return; if (row.conversation_id) { await router.replace({ path: '/assistant', query: { conversation_id: row.conversation_id, turn_id: row.answer_id } }); return }; legacy.value = row; await bottom() }
  catch (failure) { if (!signal.aborted && !cancelled(failure)) error.value = errorText(failure) }
  finally { if (!signal.aborted) loading.value = false }
}
const scopeOpen = ref(false)
const scopeError = ref('')
const question = ref(''); const error = ref(''); const notice = ref(''); const busy = ref(false); const loading = ref(false)
const snapshots = ref<KnowledgeSnapshot[]>([]); const current = ref<ConversationDetail | null>(null)
const scopeSnapshot = computed(() => snapshots.value.find(row => current.value ? row.snapshot_id === current.value.snapshot_id : snapshot.value === CURRENT_SNAPSHOT ? row.is_current : row.snapshot_id === snapshot.value))
const scopeLabel = computed(() => current.value ? '回答资料已固定' : snapshot.value === CURRENT_SNAPSHOT ? '使用当前已发布资料' : `使用历史资料 · ${scopeSnapshot.value ? timestamp(scopeSnapshot.value.published_at!) : '读取中'}`)
type PendingTurn = QuestionRequest & { accepted_answer_id?: string }
type FirstQuestion = { key: string; query: string; conversation_id?: string }
const pending = ref<PendingTurn | null>(null); const creation = ref<ConversationRequest | null>(null)
const first = ref<FirstQuestion | null>(null); const invalidCache = ref(false)
const rejected = ref(false)
const prefix = `machineguard.conversation.${auth.user?.id}`; const createKey = `${prefix}.create`; const firstKey = `${prefix}.first`
const active = computed(() => current.value?.turns.some(turn => ['queued', 'running'].includes(turn.job_status)))
const limitReached = computed(() => !!current.value && current.value.revision >= Number(current.value.config_snapshot.max_turns))
const uncertain = computed(() => !!pending.value && !pending.value.accepted_answer_id)
const navigationLocked = computed(() => busy.value || uncertain.value || !!creation.value || invalidCache.value)
watch(navigationLocked, value => { history.locked = value; emit('navigationLock', value) }, { immediate: true })
const canSend = computed(() => !route.query.legacy_answer_id && allowed.value && (!!current.value || !route.query.conversation_id) && !busy.value && !loading.value && !pending.value && !creation.value && !invalidCache.value && !active.value && !limitReached.value && !!question.value.trim())
const canChooseQuestion = computed(() => !route.query.legacy_answer_id && !!current.value && !busy.value && !loading.value && !pending.value && !invalidCache.value && !active.value && !limitReached.value)
const messages = ref<HTMLElement>(); const newResult = ref(false); const input = ref<InstanceType<typeof ElInput>>()
const inputExpanded = ref(false)
const source = ref<CitationReference | null>(null); const sourceOpen = ref(false)
const renameOpen = ref(false); const renameTitle = ref(''); const renameOriginal = ref(''); const renaming = ref(false); const renameError = ref('')
const labels: Record<string, string> = { queued: '排队中', running: '处理中', succeeded: '已完成', failed: '失败', cancelled: '已取消' }
const stages: Record<string, string> = { queued: '等待 Worker', claimed: '已领取', completed: '已完成', qa_rewrite: '理解追问', qa_retrieve: '查找资料', qa_rerank: '筛选证据', qa_generate: '生成候选回答', qa_verify: '核对引用与原意', qa_suggest: '准备追问建议', qa_save: '保存结果' }
let disposed = false; let timer: number | undefined; let controller: AbortController | undefined
let navigation = 0
const uuid = (value: unknown) => typeof value === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value)
const draftKey = () => `${prefix}.draft.${current.value?.conversation_id ?? 'new'}`
function saveDraft() { sessionStorage.setItem(draftKey(), question.value); saveNewScope() }
function saveNewScope() {
  if (!current.value && !creation.value && !invalidCache.value) sessionStorage.setItem(`${prefix}.draft-scope.new`, JSON.stringify({ snapshot: snapshot.value, equipment: equipment.value, engine: engine.value, cycle: cycle.value ?? null }))
}
function restoreNewScope() {
  const saved = sessionStorage.getItem(`${prefix}.draft-scope.new`)
  if (!saved) return
  try {
    const value = JSON.parse(saved)
    if (!value || typeof value.snapshot !== 'string' || typeof value.equipment !== 'string' || typeof value.engine !== 'string' || !(value.cycle === null || (Number.isInteger(value.cycle) && value.cycle > 0))) throw new Error('资料会话范围草稿损坏，请核对后显式清除。')
    snapshot.value = value.snapshot; equipment.value = value.equipment; engine.value = value.engine; cycle.value = value.cycle ?? undefined
  } catch (failure) { invalidCache.value = true; error.value = errorText(failure) }
}
watch([snapshot, equipment, engine, cycle], saveNewScope)
function setQuestion(value: string) { question.value = value; saveDraft(); input.value?.focus() }
function chooseQuestion(value: string) { if (!canChooseQuestion.value) return; setQuestion(value); notice.value = '已填入输入框，可以修改后发送。' }
function stop() { window.clearTimeout(timer); timer = undefined }
function schedule() {
  stop()
  // 已接收但尚未确认终态时只读取状态，不重新提交或调用模型。
  if (!disposed && (active.value || pending.value?.accepted_answer_id)) timer = window.setTimeout(() => { void refresh() }, 2000)
}
async function bottom() { await nextTick(); if (messages.value) messages.value.scrollTop = messages.value.scrollHeight; newResult.value = false }
function onScroll() { const el = messages.value; if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 80) newResult.value = false }
function restoreCreation() {
  try {
    const saved = sessionStorage.getItem(createKey); const savedFirst = sessionStorage.getItem(firstKey)
    if (saved) {
      const value = JSON.parse(saved) as ConversationRequest
      if (!uuid(value.idempotency_key) || typeof value.title !== 'string' || !value.title.trim()) throw new Error()
      creation.value = value; snapshot.value = value.snapshot_id ?? CURRENT_SNAPSHOT; equipment.value = value.equipment_type ?? ''; engine.value = value.engine_key ?? ''; cycle.value = value.end_cycle ?? undefined
    }
    if (savedFirst) {
      const value = JSON.parse(savedFirst) as FirstQuestion
      if (!uuid(value.key) || typeof value.query !== 'string' || !value.query.trim() || (creation.value && value.key !== creation.value.idempotency_key)) throw new Error()
      first.value = value
      if (creation.value) question.value = value.query
    }
  } catch { invalidCache.value = true; error.value = '本地会话请求记录损坏。请先在左侧核对已保存的会话，再显式开始新对话。' }
}
function restoreTurn(id: string) {
  pending.value = null; invalidCache.value = false; question.value = sessionStorage.getItem(draftKey()) ?? ''
  try {
    const saved = sessionStorage.getItem(`${prefix}.${id}`)
    if (!saved) return
    const value = JSON.parse(saved) as PendingTurn
    if (value.conversation_id !== id || typeof value.query !== 'string' || !value.query.trim() || !uuid(value.idempotency_key)
      || !Number.isInteger(value.expected_revision) || Number(value.expected_revision) < 0 || Number(value.expected_revision) >= 20
      || (value.accepted_answer_id !== undefined && !uuid(value.accepted_answer_id))) throw new Error()
    pending.value = value; question.value = ''; settlePending()
  } catch { invalidCache.value = true; error.value = '本地轮次记录损坏。请核对本会话全部轮次，再点击“清除损坏记录”；不会自动重发问题。' }
}
function settlePending() {
  const saved = pending.value
  if (!saved?.accepted_answer_id || !current.value) return
  const answer = current.value.turns.find(turn => turn.answer_id === saved.accepted_answer_id)
  if (!answer) return
  if (answer.question !== saved.query || answer.turn_number !== Number(saved.expected_revision) + 1) { invalidCache.value = true; error.value = '原请求与已保存轮次不一致，请核对记录。'; return }
  if (['succeeded', 'failed', 'cancelled'].includes(answer.job_status)) {
    sessionStorage.removeItem(`${prefix}.${current.value.conversation_id}`); pending.value = null
    notice.value = answer.job_status === 'succeeded' ? '本轮已完成，可以继续提问。' : '本轮已结束，未自动重试；可以发送新问题。'
  }
}
async function refresh() {
  if (!current.value || loading.value) return
  stop(); controller?.abort(); controller = new AbortController(); const signal = controller.signal
  const id = current.value.conversation_id; const el = messages.value
  const follow = !el || el.scrollHeight - el.scrollTop - el.clientHeight < 100
  const previous = JSON.stringify(current.value.turns.map(turn => [turn.answer_id, turn.job_status, turn.stage]))
  loading.value = true
  try {
    const value = await api.conversation(auth.token, id, signal)
    if (disposed || signal.aborted || current.value?.conversation_id !== id) return
    current.value = value; error.value = ''; settlePending()
    if (previous !== JSON.stringify(value.turns.map(turn => [turn.answer_id, turn.job_status, turn.stage]))) { if (follow) await bottom(); else newResult.value = true }
    if (!active.value) await history.load()
  } catch (failure) { if (!signal.aborted && !cancelled(failure) && !disposed) error.value = errorText(failure) }
  finally { if (!signal.aborted) { loading.value = false; schedule() } }
}
async function show(id: string) {
  stop(); controller?.abort(); controller = new AbortController(); const signal = controller.signal
  current.value = null; legacy.value = null; pending.value = null; creation.value = null; rejected.value = false; sourceOpen.value = false; question.value = ''; error.value = ''; notice.value = ''; loading.value = true
  try {
    const value = await api.conversation(auth.token, id, signal)
    if (disposed || signal.aborted) return
    current.value = value; restoreTurn(id); await bottom(); await focusTurn()
  } catch (failure) { if (!signal.aborted && !cancelled(failure) && !disposed) error.value = errorText(failure) }
  finally { if (!signal.aborted) { loading.value = false; schedule() } }
}
async function postTurn(body: PendingTurn) {
  const id = body.conversation_id!
  // 先持久化原请求；确认时只发送原字段，不把客户端接收编号传给服务器。
  pending.value = body; sessionStorage.setItem(`${prefix}.${id}`, JSON.stringify(body))
  const { accepted_answer_id: ignored, ...request } = body
  const answer = await api.askKnowledge(auth.token, request)
  sessionStorage.setItem(`${prefix}.${id}`, JSON.stringify({ ...body, accepted_answer_id: answer.answer_id }))
  sessionStorage.removeItem(`${prefix}.draft.${id}`)
  if (disposed || current.value?.conversation_id !== id) return
  pending.value = { ...body, accepted_answer_id: answer.answer_id }; question.value = ''
  await refresh(); await bottom()
}
async function createAndSend() {
  const startedAt = navigation
  if (!creation.value && !!engine.value.trim() !== (cycle.value !== undefined && cycle.value !== null)) throw new Error('设备代码和观测周期需要同时填写，也可以同时留空。')
  const body = creation.value ?? { title: Array.from(question.value.trim().replace(/\s+/g, ' ')).slice(0, 32).join(''), snapshot_id: snapshot.value === CURRENT_SNAPSHOT ? null : snapshot.value,
    equipment_type: equipment.value.trim() || null, engine_key: engine.value.trim() || null, end_cycle: cycle.value ?? null, idempotency_key: crypto.randomUUID() }
  if (!creation.value) {
    creation.value = body; first.value = { key: body.idempotency_key, query: question.value.trim() }
    sessionStorage.setItem(firstKey, JSON.stringify(first.value)); sessionStorage.setItem(createKey, JSON.stringify(body))
  }
  const value = await api.createConversation(auth.token, body)
  if (disposed || navigation !== startedAt) return
  if (first.value) { first.value.conversation_id = value.conversation_id; sessionStorage.setItem(firstKey, JSON.stringify(first.value)) }
  if (disposed) return
  // 导航前保存首轮请求，创建已成功而页面刷新时也能确认原轮次。
  let firstRequest: PendingTurn | null = null
  if (first.value && value.revision === 0) {
    const saved = sessionStorage.getItem(`${prefix}.${value.conversation_id}`)
    if (saved) {
      firstRequest = JSON.parse(saved) as PendingTurn
      if (firstRequest.conversation_id !== value.conversation_id || firstRequest.query !== first.value.query || !uuid(firstRequest.idempotency_key) || firstRequest.expected_revision !== 0) throw new Error('首轮本地记录不一致，请核对原会话。')
    } else {
      firstRequest = { query: first.value.query, snapshot_id: null, equipment_type: null, conversation_id: value.conversation_id, expected_revision: 0, idempotency_key: crypto.randomUUID() }
      sessionStorage.setItem(`${prefix}.${value.conversation_id}`, JSON.stringify(firstRequest))
    }
  }
  await show(value.conversation_id)
  if (!current.value || current.value.conversation_id !== value.conversation_id) { creation.value = body; throw new Error('会话已保存，但详情尚未确认，请确认原会话请求。') }
  sessionStorage.removeItem(createKey); creation.value = null
  await router.replace({ path: '/assistant', query: { conversation_id: value.conversation_id } })
  await history.load(true)
  if (disposed || current.value?.conversation_id !== value.conversation_id) return
  sessionStorage.removeItem(firstKey); sessionStorage.removeItem(`${prefix}.draft.new`); first.value = null
  if (firstRequest) await postTurn(firstRequest)
  else if (pending.value) notice.value = '原会话已有轮次请求，请先确认其结果。'
}
async function send(confirm = false) {
  if (busy.value || invalidCache.value || (!confirm && !canSend.value)) return
  // 用户主动发送的路径问题交给现有受控工具；不创建无用会话，不覆盖待确认请求。
  if (!confirm && !pending.value && !creation.value && isEvidencePathQuestion(question.value)) {
    if (!auth.user?.permissions.includes('analysis:create')) { error.value = '当前身份没有分析与查询权限。'; return }
    const key = `machineguard.analysis.request.${auth.user.id}`
    if (sessionStorage.getItem(key)) { error.value = '还有一项分析请求尚待确认，请先到“分析与查询”确认原请求。'; return }
    const id = crypto.randomUUID()
    saveDraft()
    sessionStorage.setItem(key, JSON.stringify({ question: question.value.trim(), engine_key: null, end_cycle: null, idempotency_key: id }))
    await router.push({ path: '/assistant', query: { mode: 'analysis', handoff: id } })
    return
  }
  busy.value = true; history.locked = true; error.value = ''; notice.value = ''; rejected.value = false
  try {
    if (!current.value) await createAndSend()
    else { const body = confirm && pending.value ? pending.value : { query: question.value.trim(), snapshot_id: null, equipment_type: null, conversation_id: current.value.conversation_id, expected_revision: current.value.revision, idempotency_key: crypto.randomUUID() }; await postTurn(body) }
  } catch (failure) {
    if (!disposed) {
      error.value = errorText(failure)
      // 只有明确未受理的校验或轮次冲突允许重新编辑；超时与网络丢失继续保留原键。
      rejected.value = failure instanceof ApiError && (failure.status === 422 || ['CONVERSATION_REVISION_CONFLICT', 'CONVERSATION_TURN_PENDING'].includes(failure.code))
    }
  }
  finally { busy.value = false; history.locked = navigationLocked.value; schedule() }
}
async function toggleInput() {
  // 与设备分析一致，展开仅改变编辑空间，完整草稿和轮次请求保持原状。
  inputExpanded.value = !inputExpanded.value
  await nextTick()
  // Element Plus 不会因行数配置切换自动重算高度，显式同步展开后的尺寸。
  input.value?.resizeTextarea(); input.value?.focus()
}
function keydown(event: Event | KeyboardEvent) {
  if (event instanceof KeyboardEvent && event.key === 'Enter' && event.shiftKey && !event.isComposing) inputExpanded.value = true
  if (event instanceof KeyboardEvent && event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) { event.preventDefault(); if (canSend.value) void send() }
}
async function newConversation() {
  if (navigationLocked.value) return
  saveDraft(); await router.push({ path: '/assistant', query: { mode: 'knowledge', new: crypto.randomUUID() } }); await nextTick(); input.value?.focus()
}
async function closeConversation() {
  if (navigationLocked.value) return
  saveDraft(); await router.push({ path: '/assistant', query: { mode: 'knowledge', closed: crypto.randomUUID() } })
  notice.value = '已关闭当前视图，会话与运行中的问题仍保留在历史中。'
}
function resetNew(explicit: boolean) {
  stop(); controller?.abort(); current.value = null; legacy.value = null; pending.value = null; creation.value = null; first.value = null; sourceOpen.value = false; scopeOpen.value = false; loading.value = false
  error.value = ''; notice.value = ''; invalidCache.value = false; rejected.value = false
  if (explicit) { sessionStorage.removeItem(createKey); sessionStorage.removeItem(firstKey); sessionStorage.removeItem(`${prefix}.draft.new`); sessionStorage.removeItem(`${prefix}.draft-scope.new`); snapshot.value = CURRENT_SNAPSHOT; equipment.value = ''; engine.value = ''; cycle.value = undefined; history.search = ''; void history.load(true) }
  question.value = sessionStorage.getItem(`${prefix}.draft.new`) ?? ''; if (!explicit) { restoreNewScope(); restoreCreation() }
}
function clearBroken() {
  if (active.value || busy.value) return
  if (current.value) sessionStorage.removeItem(`${prefix}.${current.value.conversation_id}`)
  else { sessionStorage.removeItem(createKey); sessionStorage.removeItem(firstKey); sessionStorage.removeItem(`${prefix}.draft-scope.new`); creation.value = null; first.value = null }
  pending.value = null; invalidCache.value = false; error.value = ''; notice.value = '已显式清除损坏的本地请求记录。'
}
async function editRejected() {
  if (!rejected.value || busy.value) return
  const query = pending.value?.query ?? first.value?.query ?? question.value
  if (current.value) {
    await refresh()
    if (active.value || loading.value || error.value) return
    sessionStorage.removeItem(`${prefix}.${current.value.conversation_id}`); pending.value = null
  } else { sessionStorage.removeItem(createKey); sessionStorage.removeItem(firstKey); creation.value = null; first.value = null }
  rejected.value = false; question.value = query; saveDraft(); error.value = ''; notice.value = '原请求未受理。已保留问题供你重新编辑，未重新调用模型。'
}
function useDeviceSelection() { scopeError.value = ''; try { selection.restore(); if (!selection.selected) throw new Error('请先在设备工作台选择设备和观测周期。'); engine.value = selection.selected.engineKey; cycle.value = selection.selected.endCycle } catch (failure) { scopeError.value = errorText(failure) } }
function clearDevice() { engine.value = ''; cycle.value = undefined; scopeError.value = '' }
async function cancel() {
  const turn = current.value?.turns.find(item => ['queued', 'running'].includes(item.job_status))
  if (!turn || busy.value) return
  busy.value = true; history.locked = true
  try { await api.cancelJob(auth.token, turn.job_id); await refresh() } catch (failure) { error.value = errorText(failure) } finally { busy.value = false; history.locked = navigationLocked.value }
}
function openSource(turn: KnowledgeAnswer, claim: number, citation: number) { source.value = { answer_id: turn.answer_id, claim_index: claim, citation_index: citation }; sourceOpen.value = true }
async function copy(turn: KnowledgeAnswer) {
  try { const value = turn.result?.status === 'refused' ? turn.result.reason ?? '' : turn.result?.claims.map(claim => `${claim.text}\n${claim.citations.map(c => `来源：${c.source_metadata.title}，版本 ${c.document_version}，页 ${c.page_spans.map(s => s.page).join('、')}`).join('\n')}`).join('\n\n') ?? ''; await navigator.clipboard.writeText(value); notice.value = '回答与来源说明已复制。' } catch { error.value = '复制失败，请检查浏览器剪贴板权限。' }
}
function openRename() { if (!current.value) return; renameTitle.value = current.value.title; renameOriginal.value = current.value.title; renameError.value = ''; renameOpen.value = true }
async function rename() {
  if (!current.value || renaming.value) return
  renaming.value = true
  try { const value = await api.renameConversation(auth.token, current.value.conversation_id, renameTitle.value.trim(), renameOriginal.value); current.value.title = value.title; renameOpen.value = false; await history.load() } catch (failure) { renameError.value = errorText(failure) } finally { renaming.value = false }
}
watch(() => [route.query.conversation_id, route.query.new, route.query.legacy_answer_id, route.query.turn_id, route.query.closed], async ([id, fresh, old]) => {
  navigation += 1
  scopeOpen.value = false
  scopeError.value = ''
  if (!allowed.value) return
  if (typeof old === 'string') await showLegacy(old)
  else if (typeof id === 'string') { if (current.value?.conversation_id !== id) await show(id); else await focusTurn() }
  else {
    // 新对话动作只消费一次；同一地址刷新继续恢复草稿和原请求。
    const explicit = typeof fresh === 'string' && sessionStorage.getItem(`${prefix}.new-action`) !== fresh
    if (explicit) sessionStorage.setItem(`${prefix}.new-action`, fresh)
    resetNew(explicit)
  }
}, { immediate: true })
onMounted(async () => { if (!allowed.value) return; try { const value = await api.knowledgeSnapshots(auth.token); if (!disposed) snapshots.value = [...new Map([...value.items, ...(value.current ? [value.current] : [])].filter(item => item.published_at).map(item => [item.snapshot_id, item])).values()] } catch (failure) { if (!disposed) error.value = errorText(failure) } })
onBeforeUnmount(() => { disposed = true; stop(); controller?.abort(); history.locked = false })
</script>

<template>
  <div v-if="!allowed" class="scope-note">当前内部资料会话仅开放给管理员。</div>
  <div v-else class="chat-layout"><aside v-if="!historyCollapsed" class="chat-history"><div class="chat-history-heading"><strong>会话</strong><el-button text aria-label="收起会话栏" @click="toggleHistory">收起</el-button></div><ConversationHistory /></aside><div class="chat-view">
    <header class="chat-heading"><div><h1>{{ legacy ? '历史回答（只读）' : current?.title || '资料问答' }}</h1><p>基于已发布资料回答 · 支持连续追问</p></div><div class="heading-actions"><el-button text aria-label="展开会话列表" @click="historyMobileOpen = true">会话</el-button><el-button v-if="historyCollapsed" class="desktop-history-toggle" text @click="toggleHistory">展开会话栏</el-button><el-button v-if="current" text :disabled="busy" @click="openRename">重命名</el-button><el-button v-if="current" text :disabled="busy || loading" @click="refresh">刷新</el-button><el-button v-if="current || legacy || route.query.conversation_id || route.query.legacy_answer_id" :disabled="navigationLocked" @click="closeConversation">关闭当前</el-button><el-button :disabled="navigationLocked" @click="newConversation">新对话</el-button></div></header>
    <ErrorPanel v-if="route.query.legacy_answer_id" :message="error" />
    <div v-if="!route.query.legacy_answer_id" class="scope-bar"><span>{{ scopeLabel }}<template v-if="current"> · {{ current.revision }} / {{ current.config_snapshot.max_turns }} 轮</template><template v-if="current ? !!current.device_context : !!engine"> · 已固定指代对象</template><template v-if="current ? !!current.equipment_type : !!equipment"> · 已限定资料类型</template></span><el-button text :disabled="busy || loading" @click="scopeOpen = true">{{ current ? '查看范围' : '设置范围' }}</el-button></div>
    <div ref="messages" class="chat-messages" @scroll="onScroll">
      <div v-if="!current && !route.query.legacy_answer_id && !loading" class="chat-welcome"><div class="welcome-symbol"><AppIcon name="chat" /></div><h2>从一个资料问题开始</h2><p>回答附有原文依据，你可以直接接着追问。</p><div class="suggestions"><button :disabled="!!creation || invalidCache" @click="setQuestion('剩余运行周期来自哪里？')">剩余运行周期来自哪里？</button><button :disabled="!!creation || invalidCache" @click="setQuestion('观测变化是否等于已确认故障？')">观测变化是否等于已确认故障？</button></div></div>
      <p v-if="loading && !current" class="chat-loading" role="status">正在读取会话</p>
      <div class="message-list"><article v-for="turn in displayTurns" :key="turn.answer_id" :id="`turn-${turn.answer_id}`" class="message-pair"><div class="user-message"><span class="sr-only">用户问题</span><p>{{ turn.question }}</p></div><div class="assistant-message"><div class="message-author"><AppIcon name="chat" /><strong>MachineGuard</strong><span>{{ timestamp(turn.created_at) }}</span></div><p v-if="['queued', 'running'].includes(turn.job_status)" class="processing-status" role="status">{{ stages[turn.stage] || labels[turn.job_status] }} · {{ turn.progress }}%</p><ErrorPanel :message="turn.error ? `${turn.error.message}（${turn.error.code}）` : ''" /><el-button v-if="turn.error && isEvidencePathQuestion(turn.question)" text type="primary" @click="setQuestion(turn.question)">将原问题填入图谱查询</el-button><p v-if="turn.job_status === 'cancelled'" class="refusal">本轮已取消，未生成回答。</p><template v-if="turn.result"><p v-if="turn.result.status === 'refused'" class="refusal">{{ turn.result.reason }}</p><div v-for="(claim, index) in turn.result.claims" :key="index" class="claim"><p>{{ claim.text }}</p><div class="citation-tags"><button v-for="(citation, i) in claim.citations" :key="i" @click="openSource(turn, index, i)">{{ citation.evidence_id }} · {{ citation.source_metadata.title }} · 版本 {{ citation.document_version }} · 页 {{ citation.page_spans.map(span => span.page).join('、') }}</button></div></div><div class="answer-actions"><button @click="copy(turn)">复制回答</button><span v-if="turn.trace.rewrite?.status === 'resolved'">已结合固定范围或历史理解追问</span></div>
              <router-link v-if="turn.result.guidance === 'device_workbench' && auth.user?.permissions.includes('analysis:create')" class="text-link workbench-link" :to="{ path: '/assistant', query: { mode: 'analysis' } }">去分析与查询，设置设备范围</router-link>
              <section v-if="turn.result.clarification_questions?.length && !legacy" class="followup-options"><strong>确认你想问的问题</strong><p>点击填入输入框，确认后发送。</p><button v-for="candidate in turn.result.clarification_questions" :key="candidate" :disabled="!canChooseQuestion" @click="chooseQuestion(candidate)">{{ candidate }}</button></section>
              <section v-if="turn.result.suggestions?.length && !legacy && turn.answer_id === displayTurns[displayTurns.length - 1]?.answer_id" class="followup-options"><strong>你可能还想问</strong><p>根据本轮资料生成；点击可编辑，不会自动发送。</p><button v-for="candidate in turn.result.suggestions" :key="candidate.question" :disabled="!canChooseQuestion" @click="chooseQuestion(candidate.question)">{{ candidate.question }}</button></section>
              <p v-if="turn.result.suggestion_error" class="suggestion-failure" role="status">本轮回答已完成，追问建议生成失败：{{ turn.result.suggestion_error.message }}（{{ turn.result.suggestion_error.code }}）</p>
            </template><AnswerProcessingDetails :answer="turn" :embedding-model="snapshots.find(row => row.snapshot_id === turn.snapshot_id)?.model" /></div></article></div>
    </div>
    <div v-if="!route.query.legacy_answer_id" class="composer-area"><button v-if="newResult" class="new-result" @click="bottom">查看新结果</button><ErrorPanel :message="error" /><p v-if="notice" class="chat-notice" role="status">{{ notice }}</p><div v-if="creation && !current && !invalidCache" class="request-recovery"><p>原会话创建结果尚待确认，保留了首条问题。确认会使用同一请求键。</p><el-button v-if="!rejected" :disabled="busy" @click="send(true)">确认原会话请求</el-button></div><div v-if="uncertain && !invalidCache" class="request-recovery"><p>原轮次“{{ pending?.query }}”的接收结果尚未确认，不会重复创建请求。</p><el-button v-if="!rejected" :disabled="busy" @click="send(true)">确认原轮次请求</el-button></div><el-button v-if="rejected" :disabled="busy || !!active" @click="editRejected">原请求未受理，重新编辑</el-button><el-button v-if="invalidCache" :disabled="!!active || busy" @click="clearBroken">已核对会话，清除损坏记录</el-button><p v-if="limitReached" class="chat-notice">已达到本会话轮数上限，请点击“新对话”继续。</p><div class="composer-options"><el-button text :aria-expanded="inputExpanded" aria-controls="chat-question" @click="toggleInput">{{ inputExpanded ? '收起输入' : '展开输入' }}</el-button></div><form class="composer" :class="{ 'is-expanded': inputExpanded }" @submit.prevent="send()"><el-input id="chat-question" ref="input" v-model="question" aria-label="会话问题" :placeholder="inputExpanded ? '询问已发布资料，或继续追问…' : '询问资料或继续追问…'" title="Enter 发送，Shift+Enter 换行；长问题可展开输入" type="textarea" resize="none" :autosize="inputExpanded ? { minRows: 4, maxRows: 6 } : { minRows: 1, maxRows: 1 }" maxlength="1000" :disabled="!!pending || !!creation || busy || !!active || invalidCache || limitReached || loading" @input="saveDraft" @keydown="keydown" /><div class="composer-toolbar"><span>Enter 发送 · Shift+Enter 换行</span><el-button v-if="active" :disabled="busy" @click="cancel">取消本轮</el-button><el-button v-else type="primary" :loading="busy" :disabled="!canSend" native-type="submit" aria-label="发送问题">发送<AppIcon name="arrow" /></el-button></div></form><p class="chat-boundary">问题与必要原文会发送到百炼。回答需人工核对；可连续追问资料；明确的图谱路径查询会转入“分析与查询”并独立保存。</p></div>
    <p v-if="route.query.legacy_answer_id" class="legacy-note">历史回答保留当时资料与引用。需要继续提问时，请新建对话。</p>
    <el-drawer v-model="historyMobileOpen" title="会话" direction="ltr" size="min(300px, 90vw)"><ConversationHistory @selected="historyMobileOpen = false" /></el-drawer>
    <el-dialog v-model="scopeOpen" title="聊天范围" width="min(600px, 94vw)" class="chat-scope-dialog">
      <div class="scope-content"><ErrorPanel :message="scopeError" />
        <template v-if="current">
          <p class="scope-explanation">本会话的资料和设备范围已固定。后续发布资料不会改变历史回答；需要修改范围时，请新建对话。</p>
          <section class="range-section"><h3>回答使用的资料</h3><p v-if="scopeSnapshot">发布于 {{ timestamp(scopeSnapshot.published_at!) }} · {{ scopeSnapshot.members.length }} 份资料</p><p v-for="member in scopeSnapshot?.members" :key="member.version_id">{{ member.source_metadata.title }} · 版本 {{ member.version_number }}</p><p>资料类型过滤：{{ current.equipment_type || '不限制' }}</p></section>
          <section class="range-section"><h3>固定的指代对象</h3><p v-if="current.device_context" class="range-device">{{ current.device_context.engine_key }} · 第 {{ current.device_context.end_cycle }} 个观测周期</p><p v-else>未固定设备。一般资料问答无需设置。</p><p class="scope-help">此设置用于理解“这台设备、这台发动机、这个周期”。具体预测值和曲线在设备工作台查看。</p></section>
          <details class="scope-advanced"><summary>会话规则与技术编号</summary><p>最多 {{ current.config_snapshot.max_turns }} 轮；指代解析使用最近 {{ current.config_snapshot.history_turns }} 轮有引用回答的历史。最近一次拒答只用于解释未回答的原因；失败、拒答与历史结论不会作为新证据。</p><p class="identifier">资料快照：{{ current.snapshot_id }}<br />配置：{{ current.config_sha256 }}</p></details>
        </template>
        <template v-else>
          <p class="scope-explanation">一般资料问答可以直接发送，无需填写设备信息。以下设置会在首次发送时固定。</p>
          <section class="range-section"><h3>回答使用的资料</h3><label class="range-field">资料范围<el-select v-model="snapshot" :disabled="!!creation || busy" aria-label="回答使用的资料"><el-option label="当前已发布资料（默认）" :value="CURRENT_SNAPSHOT" /><el-option v-for="row in snapshots.filter(item => !item.is_current)" :key="row.snapshot_id" :value="row.snapshot_id" :label="`历史资料 · ${timestamp(row.published_at!)}`" /></el-select></label><p class="scope-help">默认使用首次发送时的当前发布资料。选择历史资料，可按过去的资料范围核对问题。</p><p v-for="member in scopeSnapshot?.members" :key="member.version_id" class="scope-document">{{ member.source_metadata.title }} · 版本 {{ member.version_number }}</p></section>
          <details class="scope-advanced range-section"><summary>高级：固定指代对象（可选）</summary><p class="scope-help">仅在连续提及“这台设备、这台发动机、这个周期”时设置。它用于固定指代对象；具体预测值和曲线请到设备工作台查看。</p><p v-if="engine" class="range-device">{{ engine }} · {{ cycle == null ? '尚未选择观测周期' : `第 ${cycle} 个观测周期` }}</p><p v-else class="scope-help">未固定设备对象</p><div class="range-actions"><el-button :disabled="!!creation || busy" @click="useDeviceSelection">使用工作台当前选择</el-button><el-button v-if="engine || cycle != null" :disabled="!!creation || busy" @click="clearDevice">清除对象</el-button><router-link class="text-link" to="/devices" @click="scopeOpen = false">去设备工作台选择</router-link></div>
            <details class="scope-advanced"><summary>手动指定设备与周期</summary><div class="fields"><label>设备代码<el-input v-model="engine" aria-label="会话设备代码" maxlength="96" placeholder="使用工作台中的完整设备代码" :disabled="!!creation || busy" /></label><label>观测周期<el-input-number v-model="cycle" aria-label="会话观测周期" :min="1" :precision="0" :disabled="!!creation || busy" /></label></div><p class="scope-help">两项需要同时填写。周期是已观测数据的序号，不是日期或预计剩余寿命。</p></details>
          </details>
          <details class="scope-advanced"><summary>高级：按设备类型过滤资料</summary><label class="range-field">资料适用的设备类型<el-input v-model="equipment" aria-label="会话设备类型" maxlength="200" placeholder="一般留空，检索全部适用资料" :disabled="!!creation || busy" /></label><p class="scope-help">精确匹配资料登记的设备类型，不是设备编号。填写内容必须与知识管理中的类型一致，否则可能找不到证据。</p></details>
        </template>
      </div>
      <template #footer><el-button type="primary" @click="scopeOpen = false">{{ current ? '返回聊天' : '完成设置' }}</el-button></template>
    </el-dialog>
    <el-drawer v-model="sourceOpen" title="核对引用原文" size="min(520px, 100vw)" destroy-on-close><CitationSource v-if="source" :reference="source" :conversation-id="current?.conversation_id" management-link /></el-drawer>
    <el-dialog v-model="renameOpen" title="重命名会话" width="min(440px, 92vw)"><ErrorPanel :message="renameError" /><el-input v-model="renameTitle" aria-label="新会话名称" maxlength="100" @keydown.enter="rename" /><template #footer><el-button @click="renameOpen = false">取消</el-button><el-button type="primary" :disabled="!renameTitle.trim()" :loading="renaming" @click="rename">保存名称</el-button></template></el-dialog>
  </div>
  </div>

</template>

<style scoped>
.followup-options{display:grid;gap:8px;margin:18px 0;font-size:12px;color:#53714c}.followup-options p{font-size:11px;color:#7d8f76;line-height:1.8}.followup-options button{padding:10px 12px;border:1px solid #dce7d3;border-radius:8px;background:#f5f8f0;color:#587650;text-align:left;cursor:pointer;overflow-wrap:anywhere;line-height:1.8;font:inherit}.followup-options button:disabled{opacity:.5;cursor:default}.suggestion-failure{font-size:11px;line-height:1.8;color:#976540;margin:12px 0;overflow-wrap:anywhere}.workbench-link{display:inline-block;margin:12px 0}
.chat-view{flex:1;min-height:0;overflow:hidden;display:flex;flex-direction:column;color:#355747}.chat-heading{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:18px;padding:22px 32px 14px;flex:none}.chat-heading h1{font-size:19px;line-height:1.5;overflow-wrap:anywhere}.chat-heading p{font-size:11px;color:#92a18c;margin-top:5px}.chat-heading>div:first-child{flex:1;min-width:min(180px,100%)}.heading-actions{display:flex;gap:5px;flex:none;flex-wrap:wrap;max-width:100%;margin-left:auto}.heading-actions .el-button{margin:0;font-size:12px}.scope-bar{display:flex;justify-content:space-between;align-items:center;gap:12px;margin:0 32px 10px;padding:7px 12px;border:1px solid #e3eadd;background:#f8faf5;border-radius:8px;color:#72866e;font-size:11px;flex:none;min-width:0}.scope-bar>span{overflow-wrap:anywhere}.scope-bar .el-button{font-size:11px;margin:0;padding:6px;flex:none}.scope-content{max-height:60dvh;overflow-y:auto;font-size:13px;color:#486653;padding-right:5px}.scope-content p{line-height:1.9;margin:9px 0}.scope-explanation{padding:12px 14px;background:#f2f6ee;border-radius:8px}.range-section{padding:12px 0;border-bottom:1px solid #e6ece2}.range-section h3{margin:10px 0 14px}.range-field{display:grid;gap:8px;margin:12px 0}.scope-help{font-size:12px;color:#7b8e77}.scope-document{font-size:12px}.range-device{overflow-wrap:anywhere;font-size:13px;color:#486653}.range-actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:12px 0}.range-actions .el-button{margin:0}.scope-advanced{margin-top:15px;font-size:12px}.scope-advanced summary{cursor:pointer;color:#6c8763;line-height:1.9}.fields{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:14px 0}.fields label{display:grid;gap:7px;min-width:0}.fields .el-select{width:100%}.identifier{overflow-wrap:anywhere;font-size:10px}.chat-messages{flex:1;min-height:0;contain:layout paint;overflow-y:auto;overscroll-behavior:contain;padding:12px 28px 20px;scrollbar-gutter:stable}.message-list{max-width:var(--assistant-content-width);margin:0 auto}.message-pair{padding-bottom:30px}.user-message{display:flex;justify-content:flex-end;margin:12px 0 24px}.user-message p{background:#e8eee1;border-radius:18px 18px 3px 18px;padding:13px 20px;max-width:85%;font-size:14px;line-height:1.9;white-space:pre-wrap;overflow-wrap:anywhere}.message-author{display:flex;align-items:center;gap:9px;font-size:12px;color:#648061;margin-bottom:16px}.message-author .app-icon{width:22px;height:22px}.message-author span{margin-left:auto;font-size:10px;color:#a0ad94}.assistant-message{padding:0 4px}.claim>p,.refusal{font-size:15px;line-height:2;white-space:pre-wrap;overflow-wrap:anywhere}.claim{margin:16px 0 22px}.citation-tags{display:flex;flex-wrap:wrap;gap:8px;margin-top:13px}.citation-tags button{background:#f1f5eb;color:#658153;border:1px solid #dce7d3;border-radius:8px;font-size:11px;padding:7px 10px;text-align:left;cursor:pointer;line-height:1.7;max-width:100%;overflow-wrap:anywhere}.citation-tags button:hover{background:#e4efdb}.answer-actions{display:flex;align-items:center;gap:14px;color:#92a087;font-size:11px}.answer-actions button{border:0;background:transparent;color:#6c8b60;cursor:pointer;padding:5px 0;font:inherit}.refusal{background:#f6f4ea;padding:16px;border-radius:8px}.processing-status{font-size:13px;color:#829674;line-height:2}.composer-area{position:relative;padding:10px 30px 14px;flex:none;max-width:calc(var(--assistant-content-width) + 60px);width:100%;margin:0 auto}.composer{padding:14px 17px 10px;border:1px solid #d5e1cc;background:#fff;border-radius:18px;box-shadow:0 4px 22px #294d3510}.composer :deep(.el-textarea__inner){box-shadow:none!important;padding:3px 2px;resize:none;font-size:14px;line-height:1.8;background:transparent}.composer-toolbar{display:flex;justify-content:space-between;align-items:center;gap:10px;padding-top:8px}.composer-toolbar>span{font-size:10px;color:#a2ad96}.composer-toolbar .el-button{font-size:12px;border-radius:9px}.composer-toolbar .app-icon{width:15px;height:15px;margin-left:8px;transform:rotate(-90deg)}.chat-boundary{text-align:center;font-size:10px;color:#9ba68e;margin-top:11px;line-height:1.7}.chat-notice{font-size:11px;color:#7e966c;line-height:1.8;margin-bottom:8px;overflow-wrap:anywhere}.request-recovery{padding:12px;background:#faf3e3;border-radius:8px;margin-bottom:10px;font-size:12px;line-height:1.8}.request-recovery p{margin-bottom:10px;overflow-wrap:anywhere}.new-result{display:block;margin:-18px auto 12px;padding:9px 16px;background:#fff;border:1px solid #d8e1d1;border-radius:20px;color:#5a7a4f;cursor:pointer}.chat-welcome{max-width:760px;margin:clamp(20px,10vh,110px) auto 25px;text-align:center}.welcome-symbol{display:flex;align-items:center;justify-content:center;background:#e6efdf;color:#71915e;width:54px;height:54px;border-radius:16px;margin:0 auto 22px}.welcome-symbol .app-icon{width:26px;height:26px}.chat-welcome h2{font-size:27px;font-weight:500;letter-spacing:-.5px}.chat-welcome>p{font-size:13px;color:#92a284;margin:16px 0 28px}.suggestions{display:flex;justify-content:center;gap:12px;flex-wrap:wrap}.suggestions button{border:1px solid #dfe7d7;border-radius:10px;background:#f9fbf6;padding:13px 18px;color:#7f956d;cursor:pointer;font-size:12px}.chat-loading{text-align:center;color:#8ba080;font-size:12px;margin:32px}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
@media(max-width:760px){.chat-heading{padding:17px 16px 12px;gap:10px;align-items:flex-start}.chat-heading h1{font-size:16px}.chat-heading p{font-size:10px}.heading-actions{gap:0;flex-wrap:wrap;justify-content:flex-end;max-width:140px}.heading-actions .el-button{font-size:11px;padding:7px}.scope-bar{margin:0 16px 8px;font-size:10px}.scope-content{max-height:58dvh}.range-actions .el-button{font-size:11px}.chat-messages{padding:8px 14px 14px}.composer-area{padding:8px 12px 10px}.composer{padding:11px 13px 8px;border-radius:14px}.chat-boundary{font-size:9px}.chat-welcome{margin:38px auto 20px}.chat-welcome h2{font-size:22px}.fields{grid-template-columns:1fr}.message-author span{font-size:9px}.user-message p{font-size:13px;max-width:92%}.claim>p,.refusal{font-size:14px}.suggestions{gap:9px}.suggestions button{font-size:11px;padding:11px}}

/* 底部采用与设备分析相同的紧凑输入，多行内容通过明确展开保留。 */
.composer-area{max-width:none;padding:8px 24px;background:#fff;border-top:1px solid #e5ebdf;max-height:52dvh;overflow:auto}
.composer-options{display:flex;justify-content:flex-end;max-width:var(--assistant-content-width);margin:0 auto 4px}
.composer-options .el-button{height:24px;font-size:11px;padding:6px 10px;margin:0}
.composer{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:12px;max-width:var(--assistant-content-width);margin:0 auto;padding:8px 12px;border-radius:14px;background:#fafcf7;box-shadow:none}
.composer :deep(.el-textarea__inner){padding:0 2px;line-height:30px}
.composer-toolbar{padding-top:0}
.composer-toolbar>span{display:none}
.composer.is-expanded{display:block}
.composer.is-expanded :deep(.el-textarea__inner){padding:3px 2px;line-height:1.8}
.composer.is-expanded .composer-toolbar{padding-top:8px}
.composer.is-expanded .composer-toolbar>span{display:inline}
.chat-boundary{max-width:var(--assistant-content-width);margin:5px auto 0}
.chat-notice{max-width:var(--assistant-content-width);margin:0 auto 4px}
.new-result{margin:0 auto 6px}
@media(max-width:760px){.composer-area{padding:8px 12px}.composer{padding:7px 10px;gap:8px}.composer :deep(.el-textarea__inner){font-size:13px}.composer-toolbar .el-button{padding:8px 10px;font-size:12px}}
</style>

<style scoped>
.followup-options{display:grid;gap:8px;margin:18px 0;font-size:12px;color:#53714c}.followup-options p{font-size:11px;color:#7d8f76;line-height:1.8}.followup-options button{padding:10px 12px;border:1px solid #dce7d3;border-radius:8px;background:#f5f8f0;color:#587650;text-align:left;cursor:pointer;overflow-wrap:anywhere;line-height:1.8;font:inherit}.followup-options button:disabled{opacity:.5;cursor:default}.suggestion-failure{font-size:11px;line-height:1.8;color:#976540;margin:12px 0;overflow-wrap:anywhere}.workbench-link{display:inline-block;margin:12px 0}
.chat-layout{display:flex;flex:1;min-height:0;overflow:hidden}.chat-history{width:236px;flex:none;background:#f5f8f1;border-right:1px solid #e0e7da;padding:18px 12px;display:flex;flex-direction:column;min-height:0}.chat-history-heading{display:flex;align-items:center;justify-content:space-between;color:#56704c;margin-bottom:12px;font-size:13px}.chat-view{min-width:0}.heading-actions>[aria-label="展开会话列表"]{display:none}.legacy-note{font-size:12px;line-height:1.8;text-align:center;color:#78906d;padding:16px}.message-pair:target{outline:1px solid #aac797}@media(max-width:1050px){.chat-history{width:205px}.chat-heading{padding-left:18px;padding-right:18px}}@media(max-width:760px){.chat-history,.desktop-history-toggle{display:none}.heading-actions>[aria-label="展开会话列表"]{display:inline-flex}.chat-layout{width:100%}}
</style>
