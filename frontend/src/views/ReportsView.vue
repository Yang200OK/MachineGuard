<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElInput, ElDrawer } from 'element-plus'
import { api } from '../api'
import { reportState } from '../reportState'
import { followRun } from '../runStream'
import ReportDelivery from '../components/ReportDelivery.vue'
import { useAuthStore } from '../stores/auth'
import { cancelled, timestamp as dateTime, errorText, engineLabel } from '../format'
import type { ApprovalRequest, CitationReference, FinalReport, ReviewDetail } from '../types'
import ErrorPanel from '../components/ErrorPanel.vue'
import ReportCandidate from '../components/ReportCandidate.vue'
import CitationSource from '../components/CitationSource.vue'

const auth = useAuthStore(); const route = useRoute(); const router = useRouter()
const canReview = computed(() => auth.user?.permissions.includes('reports:review'))
const admin = computed(() => auth.user?.roles.includes('admin'))
const reviewMode = computed(() => canReview.value && route.query.mode === 'review')
const historyMode = computed(() => reviewMode.value && route.query.history === 'true')
const reviews = ref<ReviewDetail[]>([]); const reports = ref<FinalReport[]>([])
const currentReview = ref<ReviewDetail | null>(null); const currentReport = ref<FinalReport | null>(null)
const total = ref(0); const offset = ref(0); const loading = ref(false); const busy = ref(false); const error = ref('')
const comment = ref(''); const assignee = ref<string | null>(null)
const reviewers = ref<{ id: string; display_name: string }[]>([])
const pendingApproval = ref<ApprovalRequest | null>(null)
const sourceOpen = ref(false); const reference = ref<CitationReference | null>(null)
const labels: Record<string, string> = { pending: '待人工审核', approved: '已批准', rejected: '已拒绝', queued: '等待恢复', running: '正在保存', waiting_review: '待人工审核', succeeded: '已完成', failed: '执行失败', cancelled: '已取消' }
let controller: AbortController | undefined; let timer: number | undefined
const selectedId = computed(() => String(reviewMode.value ? route.query.run_id ?? '' : route.query.report_id ?? ''))
const storageKey = () => `machineguard.approval.${auth.user?.id}.${selectedId.value}`
const selectedState = computed(() => currentReview.value ? reportState(currentReview.value.review, currentReview.value.job.status) : '')

const streamMessage = ref(''); let stream: AbortController | undefined; let streamId = ''; let refresh: number | undefined
function stopStream() { stream?.abort(); stream = undefined; streamId = ''; window.clearTimeout(refresh); streamMessage.value = '' }
function connectProgress() {
  const row = currentReview.value
  if (!row || !['queued', 'running', 'waiting_review'].includes(row.job.status) || streamId === row.run_id) return
  stopStream(); streamId = row.run_id; stream = new AbortController(); let lastState = ''
  void followRun(auth.token, row.run_id, true, 0, stream.signal, {
    event: () => undefined,
    state: state => { const next = JSON.stringify(state); const job = currentReview.value?.job; if (job && (state.status !== job.status || state.stage !== job.stage || state.progress !== job.progress || lastState && next !== lastState)) { window.clearTimeout(refresh); refresh = window.setTimeout(() => void load(), 120) }; lastState = next },
    connection: message => { streamMessage.value = message },
  })
}
async function load() {
  controller?.abort(); controller = new AbortController(); const signal = controller.signal
  window.clearTimeout(timer); error.value = ''; loading.value = true
  try {
    if (reviewMode.value) {
      let page = await api.reviews(auth.token, offset.value, signal, historyMode.value ? 'history' : 'active')
      const lastOffset = Math.max(0, (Math.ceil(page.total / 10) - 1) * 10)
      if (!signal.aborted && offset.value > lastOffset) { offset.value = lastOffset; page = await api.reviews(auth.token, offset.value, signal, historyMode.value ? 'history' : 'active') }
      const detail = selectedId.value ? await api.review(auth.token, selectedId.value, signal) : null
      const people = admin.value ? await api.reviewers(auth.token, signal) : { items: [] }
      if (signal.aborted) return
      reviews.value = page.items; total.value = page.total; currentReview.value = detail
      reviewers.value = people.items; assignee.value = detail?.review.assigned_reviewer_id ?? null
      // 原请求未确认时保留确认入口；仅在正式报告已落库后自动进入报告。
      if (!historyMode.value && detail?.review.report_id && !pendingApproval.value) {
        await router.replace({ path: '/reports', query: { report_id: detail.review.report_id } }); return
      }
      if (!historyMode.value && detail && !pendingApproval.value && (detail.review.status === 'rejected' || detail.job.status === 'cancelled')) {
        await router.replace({ path: '/reports', query: { mode: 'review', history: 'true', run_id: detail.run_id } }); return
      }
      connectProgress()
      if (!detail && page.items.some(row => ['queued', 'running'].includes(row.job.status))) timer = window.setTimeout(load, 1500)
    } else {
      const page = await api.reports(auth.token, offset.value, signal)
      const detail = selectedId.value ? await api.report(auth.token, selectedId.value, signal) : null
      if (signal.aborted) return
      reports.value = page.items; total.value = page.total; currentReport.value = detail
    }
  } catch (failure) { if (!signal.aborted && !cancelled(failure)) error.value = errorText(failure) }
  finally { if (!signal.aborted) loading.value = false }
}
function select(id: string) { void router.push({ path: '/reports', query: reviewMode.value ? { mode: 'review', ...(historyMode.value ? { history: 'true' } : {}), run_id: id } : { report_id: id } }) }
function changeMode(review: boolean) { offset.value = 0; void router.push({ path: '/reports', query: review ? { mode: 'review' } : { mode: 'reports' } }) }
function changeHistory() { offset.value = 0; void router.push({ path: '/reports', query: { mode: 'review', ...(historyMode.value ? {} : { history: 'true' }) } }) }
async function assign() {
  if (!currentReview.value || busy.value) return
  busy.value = true; error.value = ''
  try { currentReview.value = await api.assignReview(auth.token, currentReview.value.run_id, assignee.value || null); await load() }
  catch (failure) { error.value = errorText(failure) }
  finally { busy.value = false }
}
async function decide(decision: 'approved' | 'rejected') {
  const row = currentReview.value
  if (!row || busy.value || (!pendingApproval.value && !comment.value.trim())) return
  if (!pendingApproval.value) {
    pendingApproval.value = { decision, comment: comment.value.trim(), idempotency_key: crypto.randomUUID(), candidate_sha256: row.review.candidate_sha256, checkpoint_id: row.checkpoint_id }
    // 超时或刷新后用原决定确认，不把未知结果当作失败重新作出决定。
    sessionStorage.setItem(storageKey(), JSON.stringify(pendingApproval.value))
  }
  busy.value = true; error.value = ''
  try {
    currentReview.value = await api.approveReport(auth.token, row.run_id, pendingApproval.value)
    sessionStorage.removeItem(storageKey()); pendingApproval.value = null; await load()
  } catch (failure) { error.value = errorText(failure) }
  finally { busy.value = false }
}
function citation(claim: number, index: number) {
  reference.value = { answer_id: reviewMode.value ? currentReview.value!.run_id : currentReport.value!.run_id, claim_index: claim, citation_index: index }; sourceOpen.value = true
}
function paginate(value: number) { offset.value = (value - 1) * 10; void load() }
watch(() => [route.query.mode, route.query.history], () => { offset.value = 0 })
watch(() => [route.query.mode, route.query.history, route.query.run_id, route.query.report_id], () => {
  stopStream(); currentReview.value = null; currentReport.value = null; sourceOpen.value = false; comment.value = ''; error.value = ''
  const saved = sessionStorage.getItem(storageKey())
  pendingApproval.value = saved ? JSON.parse(saved) as ApprovalRequest : null
  void load()
}, { immediate: true })
onBeforeUnmount(() => { stopStream(); controller?.abort(); window.clearTimeout(timer) })
</script>
<template>
  <div class="reports-page">
    <div class="page-heading"><div><h1>报告中心</h1><p>处理待审核候选，查看正式报告与审核依据。</p></div><el-button :loading="loading" :disabled="busy" @click="load">刷新</el-button></div>
    <div class="report-tabs"><el-button v-if="canReview" :type="reviewMode ? 'primary' : 'default'" :disabled="busy" @click="changeMode(true)">待审核</el-button><el-button :type="!reviewMode ? 'primary' : 'default'" :disabled="busy" @click="changeMode(false)">正式报告</el-button><el-button v-if="reviewMode" text :disabled="busy" @click="changeHistory">{{ historyMode ? '返回待审核' : '查看审核历史' }}</el-button></div>
    <ErrorPanel :message="error" />
    <div class="report-workspace">
      <aside class="report-list" aria-label="报告记录">
        <p v-if="reviewMode">{{ historyMode ? '审核历史' : admin ? '待审核与保存中' : '分配给我的待办' }}</p><p v-else>已保存的正式报告</p>
        <template v-if="reviewMode"><button v-for="row in reviews" :key="row.run_id" :disabled="busy" :class="{ selected: selectedId === row.run_id }" @click="select(row.run_id)"><strong>{{ engineLabel(row.candidate.trend.engine.engine_id) }} · 周期 {{ row.candidate.trend.end_cycle }}</strong><span>{{ row.owner_name }} · {{ reportState(row.review, row.job.status) }}</span><small>{{ dateTime(row.review.started_at) }}</small></button></template>
        <template v-else><button v-for="row in reports" :key="row.report_id" :class="{ selected: selectedId === row.report_id }" @click="select(row.report_id)"><strong>{{ engineLabel(row.content.trend.engine.engine_id) }} · 周期 {{ row.content.trend.end_cycle }}</strong><span>审核通过 · {{ row.content.review.reviewer_name }}</span><small>{{ dateTime(row.created_at) }}</small></button></template>
        <p v-if="!loading && total === 0">{{ reviewMode ? historyMode ? '暂无审核历史。' : '暂无待办，已保存的结果请到正式报告查看。' : '暂无正式报告，候选通过审核并保存成功后将在这里显示。' }}</p>
        <el-pagination v-if="total > 10" small layout="prev, pager, next" :total="total" :page-size="10" :current-page="offset / 10 + 1" @current-change="paginate" />
      </aside>
      <article class="report-detail">
        <p v-if="!currentReview && !currentReport">选择一条记录查看内容。</p>
        <template v-if="currentReview && reviewMode">
          <h2>{{ selectedState }}</h2><p v-if="streamMessage" role="status" class="review-note">{{ streamMessage }} <el-button text @click="stopStream(); connectProgress()">重新连接</el-button></p>
          <p class="review-note">{{ currentReview.owner_name }}提交 · {{ dateTime(currentReview.review.started_at) }}</p>
          <p v-if="currentReview.review.status === 'pending' && currentReview.review.approval_available" class="review-note">请核对预测、趋势、引用和使用限制，再填写审核意见。</p>
          <p v-if="currentReview.review.status === 'approved' && ['queued', 'running'].includes(currentReview.job.status)" role="status">审核意见已保存，正在保存正式报告；完成后自动打开报告。</p>
          <ErrorPanel v-if="currentReview.job.error" :message="`${currentReview.job.error.code}：${currentReview.job.error.message}`" />
          <p v-if="currentReview.review.status === 'approved' && currentReview.job.status === 'failed'" class="review-note">正式报告尚未保存成功，请联系管理员查看后台任务中的失败原因。</p>
          <div v-if="currentReview.review.decision" class="review-decision"><strong>{{ labels[currentReview.review.status] }} · {{ currentReview.review.decision.reviewer_name }}</strong><p>{{ currentReview.review.decision.comment }}</p><small>{{ dateTime(currentReview.review.decision.created_at) }}</small><p v-if="currentReview.review.status === 'rejected'">本候选已拒绝，不生成正式报告；需要修订时请新建分析。</p></div>
          <router-link v-if="currentReview.review.report_id" :to="{ path: '/reports', query: { report_id: currentReview.review.report_id } }">查看正式报告</router-link>
          <div v-if="currentReview.review.approval_available" class="review-form">
            <div v-if="admin" class="assignment"><label>分配审核员</label><el-select v-model="assignee" placeholder="管理员直接审核" clearable :disabled="busy || !!pendingApproval"><el-option v-for="person in reviewers" :key="person.id" :label="person.display_name" :value="person.id" /></el-select><el-button :disabled="busy || !!pendingApproval" @click="assign">保存分配</el-button></div>
            <label for="review-comment">审核意见（必填）</label><el-input id="review-comment" v-model="comment" type="textarea" :rows="3" :maxlength="2000" show-word-limit :disabled="busy || !!pendingApproval" placeholder="填写核对依据、使用边界，或拒绝原因。" />
            <div class="review-buttons"><el-button v-if="pendingApproval" type="primary" :loading="busy" @click="decide(pendingApproval.decision)">确认原审核请求</el-button><template v-else><el-button type="primary" :loading="busy" :disabled="!comment.trim()" @click="decide('approved')">批准并保存报告</el-button><el-button type="danger" :loading="busy" :disabled="!comment.trim()" @click="decide('rejected')">拒绝候选</el-button></template></div>
          </div>
          <el-button v-else-if="pendingApproval" :loading="busy" @click="decide(pendingApproval.decision)">确认原审核请求</el-button>
          <ReportCandidate v-if="currentReview.review.approval_available" :report="currentReview.candidate" @citation="citation" />
          <details v-else><summary>查看审核前提交版本</summary><ReportCandidate :report="currentReview.candidate" historical @citation="citation" /></details>
          <details><summary>执行与审核定位</summary><p>运行：{{ currentReview.run_id }}</p><p>模型调用 {{ currentReview.model_calls }} 次 · 工具调用 {{ currentReview.tool_calls }} 次</p><p>审核绑定内容：{{ currentReview.review.candidate_sha256 }}</p><p>图执行位置：{{ currentReview.checkpoint_id }}</p></details>
        </template>
        <template v-if="currentReport && !reviewMode">
          <div class="review-decision"><h2>正式报告已保存</h2><p>审核人：{{ currentReport.content.review.reviewer_name }} · {{ dateTime(currentReport.content.review.created_at) }}</p><small>报告编号：{{ currentReport.report_id }}</small></div>
          <ReportDelivery :key="currentReport.report_id" :report-id="currentReport.report_id" />
          <ReportCandidate :report="currentReport.content" @citation="citation" />
          <details class="report-provenance"><summary>审核记录与提交依据</summary><p>审核意见：{{ currentReport.content.review.comment }}</p><p>审核时间：{{ dateTime(currentReport.content.review.created_at) }}</p><template v-if="currentReport.provenance"><p>提交人：{{ currentReport.provenance.owner_name }} · {{ dateTime(currentReport.provenance.submitted_at) }}</p><p>提交问题：{{ currentReport.provenance.question }}</p><details><summary>查看审核前提交版本</summary><ReportCandidate :report="currentReport.provenance.candidate" historical @citation="citation" /></details><details><summary>执行与保存定位</summary><p>模型调用 {{ currentReport.provenance.model_calls }} 次 · 工具调用 {{ currentReport.provenance.tool_calls }} 次</p><p>原运行：{{ currentReport.run_id }}</p><p>审核绑定内容：{{ currentReport.content.review.candidate_sha256 }}</p><p>图执行位置：{{ currentReport.content.review.checkpoint_id }}</p><p>报告内容哈希：{{ currentReport.content_sha256 }}</p></details></template></details>
        </template>
      </article>
    </div>
    <el-drawer v-model="sourceOpen" title="核对引用原文" size="min(680px, 100vw)" destroy-on-close><CitationSource v-if="reference && sourceOpen" :reference="reference" :review-run-id="reviewMode ? currentReview?.run_id : undefined" :report-id="!reviewMode ? currentReport?.report_id : undefined" /></el-drawer>
  </div>
</template>
<style scoped>
.reports-page{color:#36584a}.page-heading{display:flex;justify-content:space-between;align-items:center;gap:16px}.page-heading h1{font-size:24px;margin:0}.page-heading p,.review-note{color:#789078;line-height:1.8}.report-tabs{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0}.report-workspace{display:grid;grid-template-columns:270px minmax(0,1fr);gap:24px}.report-list,.report-detail{background:#fff;padding:20px;border:1px solid #e0e9dc;border-radius:12px;min-width:0}.report-list button{display:flex;flex-direction:column;gap:7px;width:100%;padding:14px;margin-bottom:10px;background:#f6f8f3;border:1px solid transparent;border-radius:8px;text-align:left;color:inherit;cursor:pointer}.report-list button.selected{background:#e9f1df;border-color:#9bbfaf}.report-list span,.report-list small{font-size:12px;color:#789078}.review-decision{padding:16px;background:#edf5ec;border-radius:10px;margin:16px 0;white-space:pre-wrap;overflow-wrap:anywhere}.review-form{background:#f7f9f4;padding:18px;border-radius:10px;margin:18px 0}.review-form label{display:block;margin-bottom:10px}.review-buttons{display:flex;gap:8px;flex-wrap:wrap;margin-top:15px}.assignment{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:20px}.assignment .el-select{width:200px}.assignment label{margin:0}.report-detail details{overflow-wrap:anywhere;margin-top:22px;font-size:12px}.report-detail summary{cursor:pointer}.report-detail h2{font-size:19px}@media(max-width:900px){.report-workspace{grid-template-columns:1fr}.report-list{max-height:280px;overflow:auto}.report-detail{padding:16px}.page-heading{align-items:flex-start}.page-heading h1{font-size:21px}}
.report-provenance{border-top:1px solid #e0e9dc;padding-top:18px}.report-list button:disabled{cursor:default;opacity:.6}.report-detail details>p{margin-top:10px}.report-tabs .el-button{margin-left:0}
</style>
