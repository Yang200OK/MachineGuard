<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElDialog, ElDrawer, ElMessageBox, ElCheckbox } from 'element-plus'
import { api, ApiError } from '../api'
import { reportState } from '../reportState'
import { followRun } from '../runStream'
import { useAuthStore } from '../stores/auth'
import { useSelectionStore } from '../stores/selection'
import { cancelled, engineLabel, errorText, number, riskLabel, timestamp } from '../format'
import type { AgentRequest, AgentRun, AgentEvent, Engine, Prediction, SensorResponse, CitationReference, Page } from '../types'
import ErrorPanel from '../components/ErrorPanel.vue'
import CitationSource from '../components/CitationSource.vue'
import AnalysisHistory from '../components/AnalysisHistory.vue'
import ReportCandidate from '../components/ReportCandidate.vue'
import EvidencePaths from '../components/EvidencePaths.vue'

const emit = defineEmits<{ navigationLock: [value: boolean] }>()
const auth = useAuthStore(); const route = useRoute(); const router = useRouter()
const question = ref(''); const engine = ref<string>(); const cycle = ref<number>(); const engines = ref<Engine[]>([])
const runs = ref<AgentRun[]>([]); const current = ref<AgentRun | null>(null); const events = ref<AgentEvent[]>([])
const historyError = ref('')
const deleted = ref(false); const recordBusy = ref(false)
const total = ref(0); const page = ref(1); const error = ref(''); const submitting = ref(false); const cancelling = ref(false)
const attempt = ref<AgentRequest | null>(null); const reference = ref<CitationReference>(); const sourceOpen = ref(false)
const pathReference = ref<{ path: number; edge: number }>()
const processingOpen = ref(false)
const storageKey = `machineguard.analysis.request.${auth.user?.id}`
const draftPrefix = `machineguard.analysis.${auth.user?.id}`
const notice = ref(''); const invalidCache = ref(false); const loading = ref(false)
const input = ref<HTMLTextAreaElement>(); const messages = ref<HTMLElement>(); const newResult = ref(false)
const inputExpanded = ref(false)
const historyCollapsed = ref(localStorage.getItem('machineguard.analysis-history.collapsed') === 'true')
const historyMobileOpen = ref(false); const scopeOpen = ref(false); const trendOpen = ref(false)
const oneClickOpen = ref(false); const oneStart = ref<number>(1); const oneFields = ref<string[]>(['sensor_2', 'sensor_3']); const includeKnowledge = ref(false); const windowSize = ref<number>()
const reportCandidate = computed(() => current.value?.result?.report_candidate)
const reviewed = computed(() => !!current.value?.review?.decision || current.value?.job.status === 'cancelled')
const resultTitle = computed(() => current.value?.review ? reportState(current.value.review, current.value.job.status) : current.value?.result ? labels[current.value.result.status] : current.value ? labels[current.value.job.status] : '')
const startCycle = ref<number>(1); const fields = ref<string[]>(['sensor_2', 'sensor_3'])
const sensors = Array.from({ length: 21 }, (_, index) => `sensor_${index + 1}`)
const navigationLocked = computed(() => submitting.value || !!attempt.value || invalidCache.value || recordBusy.value)
watch(navigationLocked, value => emit('navigationLock', value), { immediate: true })
const draftScope = computed(() => engine.value ? `${engineLabel(Number(engine.value.split('-').at(-1)))} · ${cycle.value == null ? '请选择末端周期' : `末端周期 ${cycle.value}`}` : '资料与路径查询无需设备；预测与趋势请设置范围')
const pureKnowledge = computed(() => ['maintenance-qa', 'evidence-path-analysis'].includes(current.value?.selected_skill?.name ?? ''))
const reason = computed(() => current.value?.result?.reason || current.value?.result?.knowledge?.reason)
const process = computed(() => {
  const kinds = new Set(events.value.map(event => event.kind))
  return [
    { title: '确认范围', done: kinds.has('skill_selected') || kinds.has('workflow_selected') || kinds.has('scope_clarification') },
    { title: '读取数据或资料', done: kinds.has('tool_completed') },
    { title: '核对结果', done: kinds.has('evidence_validated') || current.value?.result?.status === 'completed' },
    { title: '保存结果', done: !!current.value?.result },
  ]
})
const permitted = computed(() => auth.user?.permissions.includes('analysis:create'))
const selected = computed(() => engines.value.find(x => x.engine_key === engine.value))
const pending = computed(() => current.value && ['queued', 'running'].includes(current.value.job.status))
const waitingReview = computed(() => current.value?.job.status === 'waiting_review')
const predictions = computed(() => (current.value?.result?.observations ?? []).filter(x => x.tool === 'predict_rul').map(x => x.result as unknown as Prediction))
const trends = computed(() => (current.value?.result?.observations ?? []).filter(x => x.tool === 'get_sensor_trend').map(x => x.result as unknown as SensorResponse))
const overviews = computed(() => (current.value?.result?.observations ?? []).filter(x => x.tool === 'get_engine_overview').map(x => x.result as unknown as { engine: Engine; end_cycle: number; observation: Record<string, number> }))
const histories = computed(() => (current.value?.result?.observations ?? []).filter(x => x.tool === 'get_prediction_history').map(x => x.result as unknown as Page<Prediction>))
const labels: Record<string, string> = { queued: '等待执行', running: '正在分析', waiting_review: '待人工审核', succeeded: '结果已保存', failed: '分析失败', cancelled: '已取消', completed: '分析完成', clarification: '请补充范围', refused: '当前无法回答' }
const titles: Record<string, string> = { 'engine-analysis': '设备分析', 'sensor-trend-analysis': '传感器趋势', 'maintenance-qa': '资料问答', 'evidence-path-analysis': '证据路径分析' }
const eventLabels: Record<string, string> = { submitted: '提交并固定范围', skill_selected: '模型选择分析能力', scope_clarification: '范围需要澄清', model_started: '开始模型调用', model_completed: '模型调用已返回', model_failed: '模型调用失败', tool_started: '开始调用工具', tool_completed: '工具通过核对', tool_failed: '工具执行失败', evidence_validated: '资料结论与引用核对', workflow_selected: '固定一键流程已确定', report_candidate_created: '生成未审核候选', report_validated: '候选字段与引用通过核对', completed: '保存已核对结果', failed: '分析明确失败', cancelled: '分析已取消', action_saved: '保存动作结果', action_reused: '复用已保存动作', checkpoint_recovered: '从持久化图状态恢复', review_pending: '暂停并等待人工审核' }
Object.assign(eventLabels, { review_decided: '保存人工审核决定', report_saved: '保存正式报告', paths_validated: '核对路径与逐条出处' })
const toolLabels: Record<string, string> = { get_engine_overview: '读取设备概况', predict_rul: 'LSTM 剩余周期预测', get_sensor_trend: '计算传感器趋势', get_prediction_history: '读取本人预测历史', search_maintenance_knowledge: '检索并重排固定资料', search_evidence_paths: '查询已审核证据路径' }
let controller: AbortController | undefined; let timer: number | undefined; let disposed = false
const streamMessage = ref(''); let stream: AbortController | undefined; let streamId = ''; let refresh: number | undefined
function stopStream() { stream?.abort(); stream = undefined; streamId = ''; window.clearTimeout(refresh); streamMessage.value = '' }
function connectProgress() {
  const row = current.value
  if (!row || !['queued', 'running', 'waiting_review'].includes(row.job.status)) return
  if (streamId === row.run_id) return
  stopStream(); streamId = row.run_id; stream = new AbortController()
  void followRun(auth.token, row.run_id, false, events.value.at(-1)?.sequence ?? 0, stream.signal, {
    event: event => { if (!events.value.some(item => item.sequence === event.sequence)) events.value.push(event) },
    state: state => { if (current.value && (state.event_sequence !== current.value.event_sequence || state.status !== current.value.job.status || state.stage !== current.value.job.stage || state.progress !== current.value.job.progress || state.cancel_requested !== !!current.value.job.cancel_requested_at)) { window.clearTimeout(refresh); refresh = window.setTimeout(() => void load(), 120) } },
    connection: message => { streamMessage.value = message },
  })
}
function stop() { window.clearTimeout(timer); controller?.abort() }
async function load() {
  stop(); controller = new AbortController(); const signal = controller.signal; if (!invalidCache.value) error.value = ''; loading.value = true
  historyError.value = ''; let listReady = false
  const previous = current.value; const follow = !messages.value || messages.value.scrollHeight - messages.value.scrollTop - messages.value.clientHeight < 100
  try {
    let data = await api.analyses(auth.token, (page.value - 1) * 10, signal, deleted.value)
    // 删除最后一页的末项后，回到仍有记录的页，避免列表看起来全部消失。
    const lastPage = Math.max(1, Math.ceil(data.total / 10))
    if (!signal.aborted && page.value > lastPage) { page.value = lastPage; data = await api.analyses(auth.token, (page.value - 1) * 10, signal, deleted.value) }
    if (signal.aborted) return
    runs.value = data.items; total.value = data.total; listReady = true
    const id = route.query.run_id
    if (typeof id === 'string') {
      if (current.value?.run_id !== id) { current.value = null; events.value = []; sourceOpen.value = false }
      const result = await api.analysis(auth.token, id, signal)
      if (signal.aborted) return
      current.value = result
      const trace: AgentEvent[] = []; let after = 0; let more = true
      while (more && !signal.aborted) { const batch = await api.analysisEvents(auth.token, id, after, signal); trace.push(...batch.items); after = batch.last_sequence; more = batch.has_more }
      if (!signal.aborted) {
        events.value = trace
        if (!previous || previous.run_id !== result.run_id) { await nextTick(); if (messages.value) messages.value.scrollTop = 0; newResult.value = false }
        else if (previous.job.status !== result.job.status || previous.event_sequence !== result.event_sequence) {
          // 较长的报告完成时优先展示摘要，避免自动滚到使用限制而错过预测结论。
          if (follow && result.result?.report_candidate && !previous.result?.report_candidate) { await nextTick(); if (messages.value) messages.value.scrollTop = 0; newResult.value = false }
          else if (follow) await bottom(); else newResult.value = true
        }
      }
    } else { current.value = null; events.value = [] }
    if (!signal.aborted) connectProgress()
    if (!disposed && !signal.aborted && !current.value && (runs.value.some(x => ['queued', 'running'].includes(x.job.status)))) timer = window.setTimeout(() => void load(), 2000)
  } catch (failure) { if (!signal.aborted && !cancelled(failure)) { current.value = null; events.value = []; error.value = errorText(failure); if (!listReady) historyError.value = errorText(failure) } }
  finally { if (!signal.aborted) loading.value = false }
}
async function open(row: AgentRun) {
  if (navigationLocked.value || deleted.value) return
  historyMobileOpen.value = false
  if (route.query.run_id === row.run_id) await load()
  else await router.push({ path: '/assistant', query: { mode: 'analysis', run_id: row.run_id } })
}
async function toggleTrash() {
  if (navigationLocked.value || loading.value) return
  historyError.value = ''; deleted.value = !deleted.value; page.value = 1; await load()
}
async function changeRecord(row: AgentRun) {
  if (navigationLocked.value || loading.value) return
  const restore = deleted.value; recordBusy.value = true; error.value = ''; historyError.value = ''
  try {
    if (!restore) await ElMessageBox.confirm(`删除“${row.question}”？分析将移入回收站，可恢复；正在执行时请先停止。`, '删除分析', { confirmButtonText: '移入回收站', cancelButtonText: '取消', type: 'warning' })
    if (disposed) return
    await api.analysisRecord(auth.token, row.run_id, restore)
    if (disposed) return
    if (!restore && route.query.run_id === row.run_id) {
      saveDraft(); historyMobileOpen.value = false
      await router.push({ path: '/assistant', query: { mode: 'analysis', closed: crypto.randomUUID() } })
    } else await load()
    notice.value = restore ? '分析已恢复，可从最近分析重新打开。' : '分析已移入回收站，可以恢复。'
  } catch (failure) { if (failure !== 'cancel' && failure !== 'close') historyError.value = errorText(failure) }
  finally { recordBusy.value = false }
}
function oneQuestion(start: number, columns: string[], knowledge: boolean) {
  return `一键分析：预测剩余运行周期，分析从周期 ${start} 到当前末端的 ${columns.join('、')} 趋势，生成未审核报告候选。${knowledge ? '并核对资料：剩余运行周期的单位是什么，风险等级是不是故障概率？' : ''}`
}
function openOneClick() { if (!navigationLocked.value && !pending.value) { error.value = ''; oneClickOpen.value = true } }
async function submitOneClick() {
  if (navigationLocked.value || pending.value) return
  error.value = ''
  if (!engine.value || !cycle.value || !windowSize.value || cycle.value < windowSize.value || !Number.isInteger(oneStart.value) || oneStart.value! < 1 || oneStart.value! > cycle.value || cycle.value - oneStart.value! + 1 > 1000 || !oneFields.value.length || oneFields.value.length > 6) { error.value = '请明确设备、满足预测窗口的末端周期，以及有效的趋势起点和一至六个字段。'; return }
  const options = { start_cycle: oneStart.value!, columns: [...oneFields.value], include_knowledge: includeKnowledge.value }
  attempt.value = { workflow: 'one_click', workflow_options: options, question: oneQuestion(options.start_cycle, options.columns, options.include_knowledge), engine_key: engine.value, end_cycle: cycle.value, idempotency_key: crypto.randomUUID() }
  sessionStorage.setItem(storageKey, JSON.stringify(attempt.value)); oneClickOpen.value = false
  await submit()
}
async function submit() {
  if (submitting.value || invalidCache.value || pending.value) return
  error.value = ''; submitting.value = true
  try {
    if (!attempt.value) {
      if (!question.value.trim()) throw new Error('请填写分析问题。')
      attempt.value = { question: question.value.trim(), engine_key: engine.value ?? null, end_cycle: cycle.value ?? null, idempotency_key: crypto.randomUUID() }
      // 先保存原请求；响应丢失或刷新后只能确认同一个请求，不能重复创建。
      sessionStorage.setItem(storageKey, JSON.stringify(attempt.value))
    }
    const row = await api.submitAnalysis(auth.token, attempt.value)
    sessionStorage.removeItem(draftKey())
    attempt.value = null; sessionStorage.removeItem(storageKey)
    // 提交成功后的内部导航不受发送按钮的锁定限制，且不再重复读取记录。
    if (!disposed) {
      if (route.query.run_id === row.run_id) await load()
      else await router.push({ path: '/assistant', query: { mode: 'analysis', run_id: row.run_id } })
      notice.value = '分析已受理，可以关闭视图后从历史继续查看。'
    }
  } catch (failure) {
    error.value = errorText(failure)
    if (failure instanceof ApiError && [401, 403, 422, 429].includes(failure.status)) { attempt.value = null; sessionStorage.removeItem(storageKey) }
  }
  finally { submitting.value = false }
}
async function cancelRun() {
  if (!current.value || cancelling.value) return
  cancelling.value = true
  try { await api.cancelAnalysis(auth.token, current.value.run_id); await load() }
  catch (failure) { error.value = errorText(failure) }
  finally { cancelling.value = false }
}
function reuseScope() {
  try { const selection = useSelectionStore(); selection.restore(); if (!selection.selected) throw new Error('设备工作台尚未保存设备与周期。'); engine.value = selection.selected.engineKey; cycle.value = selection.selected.endCycle; notice.value = '已填入设备工作台的选择。'; saveDraft() }
  catch (failure) { error.value = errorText(failure) }
}
function toggleHistory() { historyCollapsed.value = !historyCollapsed.value; localStorage.setItem('machineguard.analysis-history.collapsed', String(historyCollapsed.value)) }
async function toggleInput() {
  // 默认紧凑输入，把高度留给报告；展开只改变视图，不提交或改写草稿。
  inputExpanded.value = !inputExpanded.value
  await nextTick(); input.value?.focus()
}
function draftKey() { return `${draftPrefix}.draft.${typeof route.query.run_id === 'string' ? route.query.run_id : 'new'}` }
function saveDraft() { if (!attempt.value && !invalidCache.value) sessionStorage.setItem(draftKey(), JSON.stringify({ question: question.value, engine_key: engine.value ?? null, end_cycle: cycle.value ?? null })) }
const validScope = (value: Record<string, unknown>) => typeof value.question === 'string' && value.question.length <= 1000 && (value.engine_key === null || typeof value.engine_key === 'string') && (value.end_cycle === null || (Number.isInteger(value.end_cycle) && Number(value.end_cycle) > 0))
function validWorkflow(value: AgentRequest) {
  if (value.workflow == null || value.workflow === 'agent') return value.workflow_options == null
  if (value.workflow !== 'one_click' || !value.workflow_options) return false
  const opts = value.workflow_options
  return Number.isInteger(opts.start_cycle) && opts.start_cycle > 0 && Array.isArray(opts.columns) && opts.columns.length >= 1 && opts.columns.length <= 6 && new Set(opts.columns).size === opts.columns.length && opts.columns.every(field => sensors.includes(field) || /^setting_[1-3]$/.test(field)) && typeof opts.include_knowledge === 'boolean' && value.question === oneQuestion(opts.start_cycle, opts.columns, opts.include_knowledge) && typeof value.engine_key === 'string' && Number.isInteger(value.end_cycle) && Number(value.end_cycle) >= opts.start_cycle
}
function restoreDraft(fresh = false) {
  if (attempt.value || invalidCache.value) return
  question.value = ''; engine.value = undefined; cycle.value = undefined; notice.value = ''; scopeOpen.value = false; trendOpen.value = false
  if (fresh) sessionStorage.removeItem(draftKey())
  const saved = sessionStorage.getItem(draftKey())
  if (saved) {
    try {
      const value = JSON.parse(saved)
      if (!value || !validScope(value)) throw new Error('分析草稿格式不正确。')
      question.value = value.question; engine.value = value.engine_key ?? undefined; cycle.value = value.end_cycle ?? undefined
    } catch (failure) { invalidCache.value = true; error.value = errorText(failure) }
  }
}
async function focusInput() { await nextTick(); input.value?.focus() }
async function bottom() { await nextTick(); if (messages.value) messages.value.scrollTop = messages.value.scrollHeight; newResult.value = false }
function onScroll() { const el = messages.value; if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 80) newResult.value = false }
async function newAnalysis() {
  if (navigationLocked.value) return
  saveDraft(); historyMobileOpen.value = false; deleted.value = false; page.value = 1
  await router.push({ path: '/assistant', query: { mode: 'analysis', new: crypto.randomUUID() } }); await focusInput()
}
async function closeCurrent() {
  if (navigationLocked.value) return
  saveDraft(); await router.push({ path: '/assistant', query: { mode: 'analysis', closed: crypto.randomUUID() } })
  notice.value = '已关闭当前视图，历史记录保留；运行中的分析继续执行。'
}
async function prepareAgain(sameScope = false) {
  if (!current.value || navigationLocked.value || pending.value) return
  if (current.value.workflow === 'one_click' && !sameScope) {
    engine.value = current.value.scope.engine_key ?? undefined; cycle.value = current.value.scope.end_cycle ?? undefined
    oneStart.value = current.value.workflow_options!.start_cycle; oneFields.value = [...current.value.workflow_options!.columns]; includeKnowledge.value = current.value.workflow_options!.include_knowledge
    oneClickOpen.value = true; return
  }
  question.value = sameScope ? '' : current.value.question
  engine.value = pureKnowledge.value ? undefined : current.value.scope.engine_key ?? undefined
  cycle.value = pureKnowledge.value ? undefined : current.value.scope.end_cycle ?? undefined
  saveDraft(); notice.value = sameScope ? '已保留原范围，请输入新问题；发送后将创建独立分析。' : '已填入原问题和范围，可以修改后发送。'
  await focusInput()
}
async function chooseQuestion(value: string) {
  if (navigationLocked.value || pending.value) return
  question.value = value; saveDraft(); notice.value = '已填入输入框，可以修改后发送。'; await focusInput()
}
async function buildTrend() {
  if (!fields.value.length || !Number.isInteger(startCycle.value) || startCycle.value < 1 || (cycle.value != null && startCycle.value > cycle.value)) { error.value = '请选择至少一个传感器，以及范围内的起始周期。'; return }
  error.value = ''; trendOpen.value = false
  await chooseQuestion(`分析从周期 ${startCycle.value} 到当前末端的 ${fields.value.join(' 和 ')} 趋势。`)
}
function clearBroken() {
  if (submitting.value || loading.value) return
  sessionStorage.removeItem(storageKey); sessionStorage.removeItem(draftKey()); attempt.value = null; invalidCache.value = false
  restoreDraft(); error.value = ''; notice.value = '已显式清除损坏的本地记录，请重新编辑。'
}
function keydown(event: KeyboardEvent) {
  // 主动换行时展开编辑区，原来的发送和中文输入法判断保持不变。
  if (event.key === 'Enter' && event.shiftKey && !event.isComposing) inputExpanded.value = true
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) { event.preventDefault(); if (question.value.trim() && !navigationLocked.value && !pending.value) void submit() }
}
function changePage(value: number) { page.value = value; void load() }
watch([engine, cycle], () => { if (!attempt.value && !invalidCache.value) saveDraft() })
function citation(claim: number, index: number) { pathReference.value = undefined; reference.value = { answer_id: current.value!.run_id, claim_index: claim, citation_index: index }; sourceOpen.value = true }
function pathCitation(path: number, edge: number) { pathReference.value = { path, edge }; reference.value = { answer_id: current.value!.run_id, claim_index: path, citation_index: edge }; sourceOpen.value = true }
watch(() => [route.query.run_id, route.query.new, route.query.closed], async () => {
  stopStream()
  const fresh = typeof route.query.new === 'string' && sessionStorage.getItem(`${draftPrefix}.new-action`) !== route.query.new
  if (fresh) sessionStorage.setItem(`${draftPrefix}.new-action`, String(route.query.new))
  restoreDraft(fresh); sourceOpen.value = false; processingOpen.value = false; reference.value = undefined; newResult.value = false
  if (current.value?.run_id !== route.query.run_id) { current.value = null; events.value = [] }
  await load()
})
onMounted(async () => {
  if (!permitted.value) return
  try {
    const saved = sessionStorage.getItem(storageKey)
    if (saved) {
      try {
        const value = JSON.parse(saved)
        if (!value || !validScope(value) || !validWorkflow(value) || !value.question.trim() || typeof value.idempotency_key !== 'string' || !/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value.idempotency_key)) throw new Error('原分析请求记录损坏，请先核对历史记录。')
        attempt.value = value; question.value = value.question; engine.value = value.engine_key ?? undefined; cycle.value = value.end_cycle ?? undefined
      } catch (failure) { invalidCache.value = true; error.value = errorText(failure) }
    } else {
      const fresh = typeof route.query.new === 'string' && sessionStorage.getItem(`${draftPrefix}.new-action`) !== route.query.new
      if (fresh) sessionStorage.setItem(`${draftPrefix}.new-action`, String(route.query.new))
      restoreDraft(fresh)
    }
    const data = await api.engines(auth.token, 0, 100); if (!disposed) engines.value = data.items
    const registered = await api.models(auth.token); if (!registered.items.length) throw new Error('没有已发布模型，一键分析不可执行。'); windowSize.value = registered.items[0]!.configuration.window_size
    await load()
    // 只接收资料页用户主动发送的同一个请求；成功后原键被清除，刷新不重复提交。
    if (!disposed && !error.value && typeof route.query.handoff === 'string' && attempt.value?.idempotency_key === route.query.handoff && !invalidCache.value) await submit()
    if (route.query.workflow === 'one_click' && !navigationLocked.value) { reuseScope(); oneClickOpen.value = true }
  } catch (failure) { error.value = errorText(failure) }
})
onBeforeUnmount(() => { disposed = true; stopStream(); stop() })
</script>
<template>
  <p v-if="!permitted" class="scope-note">当前身份没有分析权限。</p>
  <div v-else class="analysis-layout">
    <aside v-if="!historyCollapsed" class="analysis-sidebar"><div class="sidebar-heading"><strong>分析记录</strong><el-button text @click="toggleHistory">收起</el-button></div><AnalysisHistory :runs="runs" :total="total" :page="page" :current-id="current?.run_id" :locked="navigationLocked" :loading="loading" :deleted="deleted" :error="historyError" @trash="toggleTrash" @record="changeRecord" @open="open" @fresh="newAnalysis" @refresh="load" @page="changePage" /></aside>
    <div class="analysis-main">
      <header class="analysis-heading"><div><h1>分析与查询</h1><p>设备预测、趋势与图谱路径 · 每次查询独立保存</p></div><div class="heading-actions"><el-button class="mobile-history" text @click="historyMobileOpen = true">记录</el-button><el-button v-if="historyCollapsed" class="desktop-history" text @click="toggleHistory">展开记录</el-button><el-button v-if="current || route.query.run_id" :disabled="navigationLocked" @click="closeCurrent">关闭当前</el-button><el-button :disabled="navigationLocked || !!pending || loading" @click="openOneClick">一键分析</el-button><el-button :disabled="navigationLocked" @click="newAnalysis">新分析</el-button></div></header>
      <div ref="messages" class="analysis-messages" @scroll="onScroll">
        <div v-if="!current && !route.query.run_id" class="analysis-welcome"><h2>你想查询什么？</h2><p>资料与图谱路径可直接提问；设备预测与趋势先设置范围。</p><div class="examples"><el-button :disabled="navigationLocked" @click="chooseQuestion('预测这台设备的剩余运行周期。')">预测剩余周期</el-button><el-button :disabled="navigationLocked" @click="trendOpen = true">查看传感器趋势</el-button><el-button :disabled="navigationLocked" @click="chooseQuestion('剩余运行周期的单位是什么，风险等级是不是故障概率？')">解释风险规则</el-button><el-button @click="chooseQuestion('查询知识图谱中从风险规则到critical的两跳证据路径，并逐条列出处。')">查询证据路径</el-button></div></div>
        <p v-if="loading && !current" class="method-caption" role="status">读取分析记录中…</p>
        <div class="result-content">
    <section v-if="current" :key="current.run_id" class="analysis-result" aria-label="分析结果">
      <div class="panel-heading"><h2>{{ resultTitle }}</h2><el-tag>{{ current.review ? resultTitle : labels[current.job.status] }}</el-tag></div>
      <details v-if="current.review?.report_id"><summary>查看提交问题</summary><p>{{ current.question }}</p></details><div v-else class="user-message"><p>{{ current.question }}</p></div>
      <p v-if="current.workflow === 'one_click'" class="method-caption">{{ current.workflow === 'one_click' ? current.review?.report_id ? '固定一键流程 · 已审核结果见正式报告' : current.review?.decision ? '固定一键流程 · 查看审核处理结果' : '固定一键流程 · 模型整理报告候选' : '按问题由模型选择工具' }}</p><p v-if="waitingReview" class="scope-note" role="status">报告候选待人工审核，已有结果和执行位置已保存。等待期间不占执行时间。请到报告中心核对并填写意见。</p><div v-if="waitingReview" class="result-actions"><router-link v-if="auth.user?.permissions.includes('reports:review')" :to="{ path: '/reports', query: { mode: 'review', run_id: current.run_id } }">前往待审核</router-link><el-button :loading="cancelling" @click="cancelRun">取消待审核分析</el-button></div><p v-if="current.review?.decision" class="scope-note">{{ current.review.status === 'approved' ? '候选已批准' : '候选已拒绝，不生成正式报告' }} · {{ current.review.decision.reviewer_name }}：{{ current.review.decision.comment }}</p><router-link v-if="current.review?.report_id" :to="{ path: '/reports', query: { report_id: current.review.report_id } }">查看已审核正式报告</router-link><p v-if="current.review?.report_id" class="method-caption">本次分析已生成正式报告，完整结论、引用和审核记录请在报告中心查看。</p><p v-else-if="current.review?.status === 'approved' && pending" role="status">审核意见已保存，正在保存正式报告。</p><ErrorPanel v-if="current.review && current.job.error" :message="`${current.job.error.code}：${current.job.error.message}`" /><ReportCandidate v-if="reportCandidate && !reviewed" :report="reportCandidate" @citation="citation" /><details v-if="reportCandidate && reviewed && !current.review?.report_id"><summary>查看审核前提交版本</summary><ReportCandidate :report="reportCandidate" historical @citation="citation" /></details>
      <template v-if="!reportCandidate"><p v-if="current.scope.engine_key && !pureKnowledge" class="method-caption">{{ engineLabel(Number(current.scope.engine_key.split('-').at(-1))) }} · 末端周期 {{ current.scope.end_cycle ?? '未选择' }}</p>
      <p v-if="pureKnowledge" class="method-caption">依据已发布资料回答，本次没有读取设备观测或预测。</p>
      <p v-if="streamMessage" role="status" class="method-caption">{{ streamMessage }} <el-button text @click="stopStream(); connectProgress()">重新连接</el-button></p><p v-if="pending" role="status">正在按固定范围分析，当前进度 {{ current.job.progress }}%。刷新或离开页面后可从记录继续查看。</p>
      <ErrorPanel :message="current.job.error ? `${current.job.error.message}（${current.job.error.code}）` : ''" />
      <p v-if="reason && !current.result?.evidence_paths" class="scope-note">{{ reason }}</p><p v-if="current.result?.status === 'clarification'" class="method-caption">在下方补充范围或修改问题后发送，将发起新分析。</p>
      <details v-for="overview in overviews" :key="overview.engine.engine_key" class="overview-details"><summary>查看设备概况与原始观测</summary><p>{{ engineLabel(overview.engine.engine_id) }} · {{ overview.engine.dataset }} / {{ overview.engine.split }} · 已读取周期 {{ overview.end_cycle }}</p><p class="method-caption">{{ overview.engine.source }}</p><details><summary>查看该周期原始观测</summary><dl class="observation-values"><template v-for="(value, field) in overview.observation" :key="field"><dt>{{ field }}</dt><dd>{{ value }}</dd></template></dl></details></details>
      <article v-for="prediction in predictions" :key="prediction.prediction_id" class="prediction-result"><h3>可信剩余周期预测</h3><strong class="rul-value">{{ number(prediction.business_rul) }}</strong> 运行周期<p>风险等级：{{ riskLabel[prediction.risk_level] }}。风险等级不是故障概率。</p><details><summary>查看预测记录与模型版本</summary><p>预测记录：{{ prediction.prediction_id }}</p><p>原始 RUL：{{ prediction.raw_rul }} · 业务 RUL：{{ prediction.business_rul }}</p><p>模型版本：{{ prediction.model_version }}</p><p>窗口周期：{{ prediction.window_start_cycle }}–{{ prediction.end_cycle }} · {{ prediction.postprocessing }}</p></details></article>
      <article v-for="trend in trends" :key="`${trend.start_cycle}-${trend.trends.map(row => row.field).join(',')}`"><h3>传感器数值观察 · 周期 {{ trend.start_cycle }}–{{ trend.end_cycle }}</h3><el-table :data="trend.trends"><el-table-column prop="field" label="字段" min-width="115" /><el-table-column label="单位" min-width="100"><template #default="scope">{{ scope.row.unit === '未知' || !scope.row.unit ? '资料未注明单位' : scope.row.unit }}</template></el-table-column><el-table-column label="起始值" min-width="110"><template #default="scope">{{ number(scope.row.first) }}</template></el-table-column><el-table-column label="末端值" min-width="110"><template #default="scope">{{ number(scope.row.last) }}</template></el-table-column><el-table-column label="变化" min-width="110"><template #default="scope">{{ number(scope.row.change) }}</template></el-table-column><el-table-column label="每周期斜率" min-width="130"><template #default="scope">{{ number(scope.row.slope_per_cycle, 6) }}</template></el-table-column></el-table><p class="method-caption">{{ trend.limitation }}</p></article>
      <article v-if="current.result?.knowledge?.claims.length"><h3>资料结论</h3><div v-for="(claim, index) in current.result.knowledge.claims" :key="index" class="analysis-claim"><p>{{ claim.text }}</p><el-button v-for="(ref, citationIndex) in claim.citations" :key="citationIndex" link type="primary" @click="citation(index, citationIndex)">{{ ref.source_metadata.title }} · 版本 {{ ref.document_version }} · 页 {{ ref.page_spans.map(x => x.page).join('、') }} · 查看原文</el-button></div></article>
      <article v-if="current.result?.evidence_paths"><h3>已审核证据路径</h3><EvidencePaths :result="current.result.evidence_paths" @source="pathCitation" /></article>
      <article v-for="(history, index) in histories" :key="index"><h3>本人预测历史 · 共 {{ history.total }} 条</h3><p v-if="!history.items.length">固定周期范围内没有本人预测记录。</p><el-table v-else :data="history.items"><el-table-column prop="end_cycle" label="末端周期" /><el-table-column label="业务 RUL"><template #default="scope">{{ number(scope.row.business_rul) }} 运行周期</template></el-table-column><el-table-column label="风险等级"><template #default="scope">{{ riskLabel[scope.row.risk_level as Prediction['risk_level']] }}</template></el-table-column><el-table-column prop="prediction_id" label="预测记录" min-width="240" /></el-table></article>

      </template>
      <div v-if="!pending" class="result-actions"><el-button :disabled="navigationLocked" @click="prepareAgain()">{{ current.workflow === 'one_click' ? '重新设置一键分析' : '修改问题' }}</el-button></div>
      <el-button text @click="processingOpen = true">处理详情</el-button>
    </section>
        </div>
      </div>
      <div class="analysis-composer">
        <button v-if="newResult" class="new-result" @click="bottom">查看新结果</button>
        <ErrorPanel :message="error" /><p v-if="notice" class="analysis-notice" role="status">{{ notice }}</p>
        <p v-if="attempt" class="request-recovery">原请求接收结果尚未确认，已保留原问题与范围。请使用“确认原请求”，不会重复创建分析。</p>
        <el-button v-if="invalidCache" :disabled="loading || submitting" @click="clearBroken">已核对历史，清除损坏记录</el-button>
        <div class="draft-scope"><span>{{ draftScope }}</span><div><el-button text :aria-expanded="inputExpanded" aria-controls="analysis-question" @click="toggleInput">{{ inputExpanded ? '收起输入' : '展开输入' }}</el-button><el-button :disabled="navigationLocked || !!pending" text @click="scopeOpen = true">设置范围</el-button><el-button :disabled="navigationLocked || !!pending" text @click="trendOpen = true">趋势问题</el-button></div></div>
        <form class="analysis-input" :class="{ 'is-expanded': inputExpanded }" @submit.prevent="submit"><textarea id="analysis-question" ref="input" v-model="question" maxlength="1000" :rows="inputExpanded ? 4 : 1" :disabled="navigationLocked || !!pending" aria-label="分析问题" title="Enter 发送，Shift+Enter 换行；长问题可展开输入" :placeholder="inputExpanded ? '描述新问题，例如：预测这台设备的剩余运行周期…' : '描述新问题…'" @input="saveDraft" @keydown="keydown" /><div class="compose-actions"><span>Enter 发送 · Shift+Enter 换行</span><el-button v-if="pending" :loading="cancelling" :disabled="!!current?.job.cancel_requested_at" @click="cancelRun">{{ current?.job.cancel_requested_at ? '正在确认取消' : '停止分析' }}</el-button><el-button v-else type="primary" native-type="submit" :loading="submitting" :disabled="invalidCache || (!attempt && !question.trim())">{{ attempt ? '确认原请求' : '开始分析' }}</el-button></div></form>
        <p class="analysis-boundary">新问题将发起独立分析，不承接上一轮结果；问题与必要数据会发送到百炼。</p>
      </div>
    </div>
    <el-drawer v-model="historyMobileOpen" title="本人分析记录" size="min(310px, 100vw)" direction="ltr"><AnalysisHistory :runs="runs" :total="total" :page="page" :current-id="current?.run_id" :locked="navigationLocked" :loading="loading" :deleted="deleted" :error="historyError" @trash="toggleTrash" @record="changeRecord" @open="open" @fresh="newAnalysis" @refresh="load" @page="changePage" /></el-drawer>
    <el-dialog v-model="scopeOpen" title="下一项分析的范围" width="min(560px, 94vw)"><p class="method-caption">提交后该任务范围固定；这里的修改只用于下一项任务。纯资料问题可同时留空。</p><div class="analysis-scope"><label>设备<el-select v-model="engine" clearable placeholder="请选择设备" :disabled="navigationLocked || !!pending" aria-label="分析设备" @change="cycle = undefined"><el-option v-for="item in engines" :key="item.engine_key" :value="item.engine_key" :label="engineLabel(item.engine_id)" /></el-select></label><label>末端观测周期<el-input-number v-model="cycle" :min="1" :max="selected?.last_observed_cycle" :disabled="navigationLocked || !!pending" aria-label="末端观测周期" /></label></div><el-button :disabled="navigationLocked || !!pending" @click="reuseScope">使用设备工作台选择</el-button><el-button v-if="current?.scope.engine_key && !pureKnowledge" :disabled="navigationLocked || !!pending" @click="engine = current.scope.engine_key; cycle = current.scope.end_cycle ?? undefined; saveDraft()">沿用当前分析范围</el-button><template #footer><el-button @click="scopeOpen = false; focusInput()">完成</el-button></template></el-dialog>
    <el-dialog v-model="oneClickOpen" title="一键分析与报告候选" width="min(620px, 94vw)">
      <p class="method-caption">按固定顺序读取设备、观察趋势、预测剩余周期，并生成未审核候选。这里设置的范围只用于本次新任务。</p>
      <div class="analysis-scope"><label>设备<el-select v-model="engine" aria-label="一键分析设备" placeholder="请选择设备" :disabled="navigationLocked" @change="cycle = undefined"><el-option v-for="item in engines" :key="item.engine_key" :value="item.engine_key" :label="engineLabel(item.engine_id)" /></el-select></label><label>末端周期<el-input-number v-model="cycle" aria-label="一键分析末端周期" :min="1" :max="selected?.last_observed_cycle" :disabled="navigationLocked" /></label><label>趋势起始周期<el-input-number v-model="oneStart" aria-label="一键分析起始周期" :min="1" :max="cycle" :disabled="navigationLocked" /></label><label>趋势字段<el-select v-model="oneFields" multiple :multiple-limit="6" aria-label="一键分析传感器" :disabled="navigationLocked"><el-option v-for="sensor in sensors" :key="sensor" :value="sensor" :label="sensor" /></el-select></label></div>
      <el-button :disabled="navigationLocked" @click="reuseScope">使用设备工作台选择</el-button>
      <p class="method-caption">预测至少需要 {{ windowSize ?? '尚未读取' }} 个观测周期；趋势只计算所选末端之前的数据。</p>
      <el-checkbox v-model="includeKnowledge" :disabled="navigationLocked || !auth.user?.permissions.includes('knowledge:manage')">核对资料中的 RUL 单位与风险规则</el-checkbox><p v-if="!auth.user?.permissions.includes('knowledge:manage')" class="method-caption">当前身份没有资料访问权限；可以生成仅包含设备数据的候选。</p>
      <ErrorPanel :message="error" /><template #footer><el-button @click="oneClickOpen = false">关闭</el-button><el-button type="primary" :disabled="navigationLocked || !engine || !cycle || !windowSize || cycle < windowSize || !oneFields.length" @click="submitOneClick">开始一键分析</el-button></template>
    </el-dialog>
    <el-dialog v-model="trendOpen" title="生成趋势问题" width="min(560px, 94vw)"><p class="method-caption">选择后只填入草稿，可修改后发送。设备与末端周期在“设置范围”选择。</p><div class="analysis-scope"><label>起始周期<el-input-number v-model="startCycle" :min="1" :max="cycle" :disabled="navigationLocked || !!pending" /></label><label>传感器<el-select v-model="fields" multiple :disabled="navigationLocked || !!pending" aria-label="趋势传感器"><el-option v-for="sensor in sensors" :key="sensor" :value="sensor" :label="`传感器 ${sensor.split('_')[1]}（${sensor}）`" /></el-select></label></div><ErrorPanel :message="error" /><template #footer><el-button @click="trendOpen = false">关闭</el-button><el-button type="primary" :disabled="navigationLocked || !!pending" @click="buildTrend">填入问题</el-button></template></el-dialog>
    <el-drawer v-model="processingOpen" title="分析处理详情" size="min(680px, 100vw)" destroy-on-close>
      <div v-if="current" class="analysis-trace"><p>{{ current.model_calls }} 次模型调用 · {{ current.tool_calls }} 次工具调用</p><ul class="process-summary"><li v-for="step in process" :key="step.title">{{ step.title }} · {{ step.done ? '已确认' : '未完成' }}</li></ul><details><summary>完整执行轨迹与版本</summary><p v-if="current.selected_skill">使用能力：{{ titles[current.selected_skill.name] }} · {{ current.selected_skill.version }}</p><ol><li v-for="event in events" :key="event.sequence"><strong>{{ eventLabels[event.kind] ?? event.kind }}</strong><span v-if="typeof event.details.tool === 'string'"> · {{ toolLabels[event.details.tool] ?? event.details.tool }}</span><span v-if="event.details.code"> · {{ event.details.code }}</span><small>{{ timestamp(event.created_at) }}</small></li></ol></details><details><summary>技术定位记录</summary><pre>{{ JSON.stringify({ run_id: current.run_id, recovery: current.recovery, review: current.review, scope: current.scope, selected_skill: current.selected_skill, events }, null, 2) }}</pre></details></div>
    </el-drawer>
    <el-drawer v-model="sourceOpen" title="核对引用原文" size="min(680px, 100vw)" destroy-on-close><CitationSource v-if="reference && current && sourceOpen" :reference="reference" :agent-run-id="current.run_id" :path-reference="pathReference" /></el-drawer>
  </div>
</template>
<style scoped>
.analysis-layout{display:flex;flex:1;min-height:0;min-width:0;overflow:hidden;color:#355747}.analysis-sidebar{width:236px;flex:none;padding:18px 12px;background:#f5f8f1;border-right:1px solid #e0e7da;display:flex;flex-direction:column;min-height:0}.sidebar-heading{display:flex;justify-content:space-between;align-items:center;font-size:13px;margin-bottom:12px}.analysis-main{display:flex;flex:1;flex-direction:column;min-width:0;min-height:0;overflow:hidden}.analysis-heading{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px;padding:18px 28px 12px;flex:none}.analysis-heading>div:first-child{flex:1;min-width:min(180px,100%)}.analysis-heading h1{font-size:19px}.analysis-heading p{font-size:11px;color:#849479;margin-top:6px}.heading-actions{display:flex;max-width:100%;margin-left:auto;gap:6px;flex-wrap:wrap;justify-content:flex-end}.heading-actions .el-button{margin:0;font-size:12px}.mobile-history{display:none}.analysis-messages{flex:1;min-height:0;overflow:auto;overscroll-behavior:contain;padding:12px 28px 20px;scrollbar-gutter:stable}.result-content{max-width:var(--assistant-content-width);margin:0 auto}.analysis-welcome{max-width:760px;margin:clamp(24px,9vh,100px) auto 25px;text-align:center}.analysis-welcome h2{font-size:25px;font-weight:500}.analysis-welcome p{font-size:13px;color:#849479;margin:18px 0 26px}.examples{display:flex;flex-wrap:wrap;gap:12px;justify-content:center}.examples .el-button{margin:0}.analysis-result{line-height:1.9;font-size:14px;overflow-wrap:anywhere}.analysis-result h2{font-size:16px}.user-message{display:flex;justify-content:flex-end;margin:12px 0 24px}.user-message p{background:#e8eee1;padding:12px 18px;max-width:90%;border-radius:16px 16px 3px 16px;white-space:pre-wrap}.analysis-result article{margin:22px 0}.analysis-result h3{font-size:14px;margin-bottom:12px}.prediction-result{background:#eef5ed;padding:20px;margin:20px 0;border-radius:10px}.rul-value{font-size:32px;color:#254d37}.analysis-result details{font-size:12px;overflow-wrap:anywhere}.analysis-result summary{cursor:pointer}.overview-details{margin:18px 0}.analysis-claim{padding:10px 0;border-bottom:1px solid #e1e9e2}.analysis-claim .el-button{white-space:normal;height:auto;text-align:left;line-height:1.8}.result-actions{display:flex;gap:10px;flex-wrap:wrap;margin:20px 0}.result-actions .el-button{margin:0;font-size:12px}.analysis-trace{border-top:1px solid #e1e9e2;padding-top:14px}.analysis-trace li{padding:6px 0}.analysis-trace small{display:block;color:#7b9284}.analysis-trace pre{white-space:pre-wrap;max-height:300px;overflow:auto;font-size:11px}.process-summary{display:flex;gap:18px;flex-wrap:wrap;list-style:none;padding:12px 0;color:#70866a}.analysis-composer{flex:none;padding:10px 24px 12px;background:#fff;border-top:1px solid #e5ebdf;max-height:52dvh;overflow:auto}.draft-scope{display:flex;align-items:center;justify-content:space-between;gap:8px;max-width:var(--assistant-content-width);margin:0 auto 5px;font-size:11px;color:#70866a}.draft-scope>span{overflow-wrap:anywhere}.draft-scope .el-button{margin:0;font-size:11px}.analysis-input{max-width:var(--assistant-content-width);margin:0 auto;padding:12px 16px;border:1px solid #d7e3db;border-radius:14px;background:#fafcf7}.analysis-input textarea{display:block;box-sizing:border-box;width:100%;resize:vertical;min-height:52px;max-height:150px;border:0;padding:0;color:#294b3c;font:inherit;line-height:1.8;background:transparent;outline:none}.compose-actions{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:8px}.compose-actions>span{font-size:10px;color:#91a186}.analysis-boundary{font-size:10px;text-align:center;color:#8a9b81;line-height:1.7;margin-top:8px}.analysis-notice{font-size:12px;color:#688557;margin:0 auto 8px;max-width:var(--assistant-content-width);line-height:1.8}.request-recovery{font-size:12px;line-height:1.8;padding:10px;background:#faf3e3;border-radius:8px;margin-bottom:8px}.new-result{display:block;margin:0 auto 10px;padding:8px 14px;background:#fff;border:1px solid #d8e1d1;border-radius:18px;color:#5a7a4f;cursor:pointer}.analysis-scope{display:flex;flex-wrap:wrap;gap:16px;margin:20px 0}.analysis-scope label{display:grid;gap:8px;font-size:13px}.analysis-scope .el-select{width:240px}@media(max-width:1050px){.analysis-sidebar{width:205px}}@media(max-width:760px){.analysis-sidebar,.desktop-history{display:none}.mobile-history{display:inline-flex}.analysis-heading{padding:12px 14px;align-items:flex-start}.analysis-heading>div:first-child{flex:1;min-width:min(180px,100%)}.analysis-heading h1{font-size:16px}.heading-actions{max-width:155px;gap:3px}.heading-actions .el-button{font-size:11px;padding:7px}.analysis-messages{padding:8px 14px 14px}.analysis-composer{padding:8px 12px 10px}.analysis-welcome{margin:30px auto 20px}.analysis-welcome h2{font-size:21px}.analysis-scope label,.analysis-scope .el-select{width:100%}.draft-scope{font-size:10px}.analysis-boundary{font-size:9px}.analysis-input{padding:10px 12px}.result-actions .el-button{font-size:11px}}

/* 紧凑输入默认只占一行；多行编辑时恢复原发送和换行提示。 */
.analysis-composer{padding-top:8px;padding-bottom:8px}
.analysis-input{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:12px;padding:8px 12px}
.analysis-input textarea{min-height:30px;line-height:30px;resize:none}
.compose-actions{margin-top:0}
.compose-actions>span{display:none}
.analysis-input.is-expanded{display:block}
.analysis-input.is-expanded textarea{min-height:96px;line-height:1.8;resize:vertical}
.analysis-input.is-expanded .compose-actions{margin-top:8px}
.analysis-input.is-expanded .compose-actions>span{display:inline}
.draft-scope{flex-wrap:wrap;margin-bottom:4px}
.draft-scope>div{display:flex;flex-wrap:wrap;justify-content:flex-end}
.analysis-notice{font-size:11px;margin-bottom:4px}
.analysis-boundary{margin-top:5px}
@media(max-width:760px){.analysis-input{padding:7px 10px;gap:8px}.analysis-input textarea{font-size:13px}.analysis-input.is-expanded textarea{min-height:80px}.compose-actions .el-button{padding:8px 10px;font-size:12px}}
</style>
