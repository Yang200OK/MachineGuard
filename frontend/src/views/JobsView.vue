<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { useAuthStore } from '../stores/auth'
import { cancelled, errorText, timestamp } from '../format'
import type { Job, JobStatus, Model } from '../types'
import ErrorPanel from '../components/ErrorPanel.vue'
import { ElMessageBox, ElDrawer, ElDialog } from 'element-plus'

const auth = useAuthStore(); const route = useRoute(); const router = useRouter()
const jobs = ref<Job[]>([]); const current = ref<Job | null>(null); const model = ref<Model | null>(null)
const total = ref(0); const page = ref(1); const loading = ref(false); const submitting = ref(false)
const modelDialogOpen = ref(false); const lastModelCheck = ref<Job | null>(null)
const detailOpen = computed({ get: () => typeof route.query.job_id === 'string', set: value => { if (!value) void closeDetails() } })
const recycled = ref(false); const selectedJobs = ref<Job[]>([]); const managing = ref(false)
const cancelling = ref(false); const error = ref(''); const notice = ref('')
const canSubmit = computed(() => auth.user?.permissions.includes('system:read'))
const pending = computed(() => current.value && ['queued', 'running', 'waiting_review'].includes(current.value.status))
const labels: Record<JobStatus, string> = { queued: '等待 Worker', running: '执行中', waiting_review: '待人工审核', succeeded: '已完成', failed: '失败', cancelled: '已取消' }
const stages: Record<string, string> = { render_report: '生成固定报告文件', upload_report: '保存私有报告对象', report_export_verified: '报告文件回读校验通过', review_resume: '等待恢复人工决定', report_saving: '核对并保存正式报告', agent_checkpoint_recovered: '恢复已保存的执行位置', waiting_review: '报告候选待人工审核', queued: '排队', claimed: '已领取', recovering: '等待重领', verify_manifest: '文件与 CPU 权重校验', verify_registration: '发布登记校验', verified: '校验完成', completed: '结果已保存', read_original: '读取并核对原文', parse_native: '普通解析与切片', parse_vision: '视觉结构解析与切片', save_parse: '原子保存解析结果', index_sources: '固定审核来源', index_embedding: '百炼文本向量', index_publish: '原子发布知识快照' }
Object.assign(stages, { agent_submitted: '固定分析范围', agent_model_started: '模型调用', agent_model_completed: '模型调用已返回', agent_model_failed: '模型调用失败', agent_skill_selected: '选择分析能力', agent_scope_clarification: '澄清分析范围', agent_tool_started: '执行工具', agent_tool_completed: '工具通过核对', agent_tool_failed: '工具失败', agent_evidence_validated: '资料证据通过核对', agent_validating: '最终结果核对', qa_rewrite: '解析会话指代', qa_retrieve: '召回资料', qa_rerank: '重排证据', qa_generate: '回答候选', qa_verify: '结论支持核对', qa_save: '保存问答结果' })
let controller: AbortController | undefined; let timer: number | undefined; let disposed = false

function stopTimer() { window.clearTimeout(timer); timer = undefined }
async function load() {
  stopTimer(); controller?.abort(); controller = new AbortController(); const signal = controller.signal
  loading.value = true; error.value = ''
  try {
    const data = await api.jobs(auth.token, (page.value - 1) * 10, 10, signal, recycled.value)
    if (signal.aborted) return
    jobs.value = data.items; total.value = data.total
    // 详情关闭后仍以列表返回的真实状态更新上次检查，不要求反复打开确认。
    const lastCheck = jobs.value.find(job => job.job_id === lastModelCheck.value?.job_id)
    if (lastCheck) lastModelCheck.value = lastCheck
    const selected = route.query.job_id
    if (typeof selected === 'string' && !recycled.value) {
      // 清除此前任务的详情，越权或失败时不能显示旧任务结论。
      current.value = null
      const result = await api.job(auth.token, selected, signal)
      if (!signal.aborted) { current.value = result; if (result.kind === 'model_integrity_check' && route.query.request_key) lastModelCheck.value = result }
    } else current.value = null
    if (canSubmit.value && !model.value) {
      const available = await api.models(auth.token, signal)
      if (available.items.length !== 1) throw new Error('模型列表不符合当前单发布模型的配置，请核对后端。')
      if (!signal.aborted) model.value = available.items[0]!
    }
    if (!disposed && !signal.aborted && (pending.value || jobs.value.some(job => ['queued', 'running'].includes(job.status)))) timer = window.setTimeout(() => void load(), 2000)
  } catch (failure) { if (!cancelled(failure)) error.value = errorText(failure) }
  finally { if (!signal.aborted) loading.value = false }
}

async function submit(fresh = false) {
  if (!model.value || submitting.value) return
  submitting.value = true; error.value = ''; notice.value = ''; stopTimer()
  try {
    // 请求键与模型版本先保存到本页 URL；超时和刷新后仍可确认同一任务。
    const priorKey = route.query.request_key; const priorVersion = route.query.model_version
    const key = !fresh && typeof priorKey === 'string' ? priorKey : crypto.randomUUID()
    const version = !fresh && typeof priorVersion === 'string' ? priorVersion : model.value.model_version
    await router.replace({ query: { ...route.query, request_key: key, model_version: version } })
    const result = await api.submitModelCheck(auth.token, version, key)
    if (disposed) return
    await router.replace({ query: { request_key: key, model_version: version, job_id: result.job_id } })
    notice.value = result.replayed ? '已确认原任务，请求未重复入队。' : '任务已提交，由独立 Worker 执行。'
    modelDialogOpen.value = false
    await load()
  } catch (failure) { if (!disposed) error.value = errorText(failure) }
  finally { submitting.value = false }
}
async function cancel() {
  if (!current.value || cancelling.value) return
  cancelling.value = true; error.value = ''; stopTimer()
  try {
    current.value = await api.cancelJob(auth.token, current.value.job_id)
    notice.value = current.value.status === 'cancelled' ? '任务已取消。' : '已请求取消，等待 Worker 在阶段边界确认。'
    await load()
  } catch (failure) { if (!disposed) error.value = errorText(failure) }
  finally { cancelling.value = false }
}
async function navigateAnswer(row: Job) {
  if (!row.result?.answer_id || row.answer_available === false) return
  try {
    const target = await api.answerNavigation(auth.token, String(row.result.answer_id))
    await router.push({ path: '/assistant', query: target.conversation_id
      ? { conversation_id: target.conversation_id, turn_id: target.answer_id }
      : { legacy_answer_id: target.answer_id } })
  } catch (failure) { error.value = errorText(failure) }
}
async function manageRecords(rows: Job[]) {
  if (!rows.length || managing.value) return
  try {
    if (!recycled.value) await ElMessageBox.confirm(`将 ${rows.length} 条已结束任务移入回收站？结果与执行记录会保留，可以恢复。`, '删除任务记录', { confirmButtonText: '移入回收站', cancelButtonText: '取消', type: 'warning' })
    managing.value = true
    const result = await api.jobRecords(auth.token, rows.map(row => row.job_id), recycled.value)
    const skipped = result.items.filter(item => !item.changed)
    notice.value = skipped.length ? `已处理 ${result.items.length - skipped.length} 条，${skipped.length} 条执行中任务未删除。` : recycled.value ? '任务记录已恢复。' : '任务记录已移入回收站。'
    page.value = 1; await router.replace({ query: {} }); current.value = null; selectedJobs.value = []; await load()
  } catch (failure) { if (failure !== 'cancel' && failure !== 'close') error.value = errorText(failure) }
  finally { managing.value = false }
}
async function switchRecords() {
  recycled.value = !recycled.value; page.value = 1; selectedJobs.value = []; current.value = null
  await router.replace({ query: {} }); await load()
}
async function closeDetails() {
  // 关闭只清除选中的任务，保留请求键用于确认原提交；不会取消后台任务。
  stopTimer(); controller?.abort(); loading.value = false; current.value = null
  const query = { ...route.query }; delete query.job_id
  await router.replace({ query })
}
async function selectJob(row: Job, column?: { type?: string }) {
  if (recycled.value || column?.type === 'selection') return
  notice.value = ''; current.value = null
  await router.replace({ query: { job_id: row.job_id } })
}
watch(() => route.query.job_id, () => { if (!submitting.value) void load() })
onMounted(load)
onBeforeUnmount(() => { disposed = true; stopTimer(); controller?.abort() })
</script>
<template>
  <div class="page-heading"><div><div class="eyebrow">TASKS & WORKER</div><h1>后台任务</h1><p>任务状态、尝试记录和校验结果保存于数据库；刷新后继续查询。</p></div><div><el-button v-if="canSubmit" @click="modelDialogOpen = true">检查模型文件</el-button><el-button :loading="loading" :disabled="submitting || cancelling" @click="load">刷新任务</el-button></div></div>
  <ErrorPanel :message="error" retry @retry="load" />
  <el-dialog v-model="modelDialogOpen" title="检查已发布模型文件" width="min(600px, 96vw)" :close-on-click-modal="false" :show-close="!submitting" :close-on-press-escape="!submitting">
    <p>部署后或怀疑模型文件损坏时使用。检查文件哈希、CPU 权重加载、预处理配置和发布登记是否一致。</p>
    <p class="method-caption">这是文件完整性检查，不评估预测准确率，也不重新训练或生成预测。日常问答无需反复运行。</p>
    <p v-if="model" class="mono">模型版本：{{ model.model_version }}</p><ErrorPanel :message="error" />
    <template #footer><el-button :disabled="submitting" @click="modelDialogOpen = false">关闭</el-button><el-button type="primary" :disabled="!model || cancelling" :loading="submitting" @click="submit()">{{ route.query.request_key ? '确认上次检查' : '提交检查' }}</el-button><el-button v-if="route.query.request_key && lastModelCheck && !['queued', 'running'].includes(lastModelCheck.status)" :disabled="submitting" @click="submit(true)">创建新检查</el-button></template>
  </el-dialog>
  <div v-if="notice" role="status" class="scope-note">{{ notice }}</div>
  <el-drawer v-model="detailOpen" title="任务详情" size="min(760px, 100vw)" :close-on-click-modal="false"><el-button @click="detailOpen = false">关闭详情</el-button><ErrorPanel :message="error" retry @retry="load" /><p v-if="loading && !current">正在读取任务。</p>
  <section v-if="current" class="job-detail"><div class="panel-heading"><div><h2>{{ current.kind === 'report_export' ? '报告导出' : current.kind === 'agent_run' ? '智能分析' : current.kind === 'model_integrity_check' ? '模型完整性校验' : current.kind === 'knowledge_index' ? '知识索引与快照发布' : current.kind === 'knowledge_answer' ? '知识资料问答' : '文档解析' }}</h2></div><el-tag :type="current.status === 'failed' ? 'danger' : current.status === 'succeeded' ? 'success' : 'info'">{{ labels[current.status] }}</el-tag></div>
    <p v-if="current.status === 'waiting_review'" class="scope-note">报告候选与执行位置已保存，等待人工审核。等待不占执行时间，请在报告中心完成审核。</p>
    <ErrorPanel :message="current.status === 'failed' && current.error ? `${current.error.message}（${current.error.code}）` : ''" />
    <p v-if="current.status === 'cancelled' && current.error" class="method-caption">{{ current.error.message }}</p>
    <p>{{ stages[current.stage] ?? current.stage }} · {{ current.progress }}%</p><details><summary>任务时间与运行信息</summary><dl class="detail-grid"><dt>任务编号</dt><dd class="mono">{{ current.job_id }}</dd><dt>当前阶段</dt><dd>{{ stages[current.stage] ?? current.stage }} · {{ current.progress }}%</dd><dt>{{ current.kind === 'report_export' ? '正式报告' : current.kind === 'agent_run' ? '分析记录' : current.kind === 'model_integrity_check' ? '模型版本' : ['knowledge_index', 'knowledge_answer'].includes(current.kind) ? '知识快照' : '文档版本' }}</dt><dd class="mono">{{ current.kind === 'report_export' ? current.payload.report_id : current.kind === 'agent_run' ? current.payload.run_id : current.kind === 'model_integrity_check' ? current.payload.model_version : ['knowledge_index', 'knowledge_answer'].includes(current.kind) ? current.payload.snapshot_id : current.payload.version_id }}</dd><dt>尝试次数</dt><dd>{{ current.attempt_count }} / {{ current.max_attempts }}</dd><dt>提交时间</dt><dd>{{ timestamp(current.created_at) }}</dd><dt>{{ current.status === 'waiting_review' ? '暂停前执行截止' : '任务时限' }}</dt><dd>{{ timestamp(current.deadline_at) }}</dd><dt>最近心跳</dt><dd>{{ current.heartbeat_at ? timestamp(current.heartbeat_at) : '尚未领取' }}</dd><dt>结束时间</dt><dd>{{ current.finished_at ? timestamp(current.finished_at) : '尚未结束' }}</dd></dl></details>
    <el-button v-if="pending" :disabled="!!current.cancel_requested_at || submitting" :loading="cancelling" @click="cancel">{{ current.cancel_requested_at ? '等待取消确认' : '取消当前任务' }}</el-button>
    <div v-if="current.kind === 'model_integrity_check' && current.status === 'succeeded' && current.result"><h3>已保存的校验结果</h3><p>正式训练 {{ current.result.completed_epochs }} 轮，最佳权重第 {{ current.result.best_epoch }} 轮，CPU 加载与发布登记通过。</p><details><summary>文件校验指纹</summary><dl class="detail-grid"><dt>清单 SHA-256</dt><dd class="mono">{{ current.result.manifest_sha256 }}</dd><dt>权重 SHA-256</dt><dd class="mono">{{ current.result.weight_sha256 }}</dd></dl></details><p class="method-caption">{{ current.result.limitation }}</p></div>
    <div v-if="['document_parse', 'document_visual_parse'].includes(current.kind) && current.status === 'succeeded' && current.result"><h3>已保存的解析结果</h3><p>父页面 {{ current.result.page_count }}，切片 {{ current.result.chunk_count }}；保存时待核对。当前审核与停用状态请在知识管理查看。</p><p class="mono">{{ current.result.parse_id }}</p></div>
    <div v-if="current.kind === 'knowledge_index' && current.status === 'succeeded' && current.result"><h3>已发布知识快照</h3><p>{{ current.result.chunk_count }}切片，{{ current.result.dimension }}维；向量和关键词索引已与发布指针一同保存。</p><p class="mono">{{ current.result.snapshot_id }}</p><p class="mono">清单 SHA-256：{{ current.result.manifest_sha256 }}</p><router-link to="/knowledge">前往知识管理检索预览</router-link></div>
    <p v-if="current.kind === 'report_export'"><RouterLink :to="{ path: '/reports', query: { report_id: current.payload.report_id } }">查看正式报告与导出文件</RouterLink></p>
    <p v-if="current.kind === 'agent_run' && current.payload.run_id"><RouterLink class="text-link" :to="{ path: '/analysis', query: { run_id: current.payload.run_id } }">查看智能分析结果与调用过程</RouterLink></p>
    <div v-if="current.kind === 'knowledge_answer' && current.status === 'succeeded' && current.result"><h3>{{ current.result.answer_status === 'refused' ? '资料不足，已拒答' : '资料回答已保存' }}</h3><p>{{ current.result.citation_count }}条引用，结果按固定知识快照保存。</p><el-button v-if="current.answer_available !== false" link type="primary" @click="navigateAnswer(current)">查看所属对话与原文引用</el-button><p v-else class="method-caption">所属会话已移入回收站，恢复会话后可以查看。</p></div>
    <details><summary>执行记录</summary><el-table :data="current.attempts"><el-table-column prop="number" label="次数" width="65" /><el-table-column label="状态" min-width="125"><template #default="scope">{{ scope.row.status === 'lease_expired' ? '租约到期' : scope.row.status === 'paused' ? '已暂停待审' : labels[scope.row.status as JobStatus] }}</template></el-table-column><el-table-column label="阶段" min-width="160"><template #default="scope">{{ stages[scope.row.stage] ?? scope.row.stage }}</template></el-table-column><el-table-column prop="worker_id" label="Worker" min-width="200" /><el-table-column prop="error_code" label="错误编号" min-width="160" /></el-table>
  </details></section></el-drawer>
  <section class="panel job-list"><div class="panel-heading"><h2>{{ recycled ? '任务回收站' : '本人的任务记录' }}</h2><div><el-button :disabled="managing" @click="switchRecords">{{ recycled ? '返回任务记录' : '回收站' }}</el-button><el-button :disabled="!selectedJobs.length || managing" @click="manageRecords(selectedJobs)">{{ recycled ? '恢复所选' : '删除所选' }}</el-button><span class="muted"> {{ total }} 项</span></div></div>
    <el-table :data="jobs" @selection-change="selectedJobs = $event" @row-click="selectJob"><el-table-column type="selection" width="45" :selectable="(row: Job) => recycled || !['queued', 'running', 'waiting_review'].includes(row.status)" /><el-table-column label="任务" min-width="150"><template #default="scope">{{ scope.row.kind === 'report_export' ? '报告导出' : scope.row.kind === 'agent_run' ? '智能分析' : scope.row.kind === 'knowledge_answer' ? '资料问答' : scope.row.kind === 'knowledge_index' ? '资料发布' : scope.row.kind === 'model_integrity_check' ? '模型校验' : '文档解析' }}</template></el-table-column><el-table-column label="提交时间" min-width="185"><template #default="scope">{{ timestamp(scope.row.created_at) }}</template></el-table-column><el-table-column label="状态" min-width="110"><template #default="scope">{{ labels[scope.row.status as JobStatus] }}</template></el-table-column><el-table-column label="操作" width="160"><template #default="scope"><el-button v-if="!recycled" text @click.stop="selectJob(scope.row)">查看</el-button><el-button text :disabled="managing || (!recycled && ['queued', 'running'].includes(scope.row.status))" @click.stop="manageRecords([scope.row])">{{ recycled ? '恢复' : '删除' }}</el-button></template></el-table-column></el-table><el-pagination v-if="total > 10" v-model:current-page="page" :page-size="10" :total="total" layout="prev, pager, next" @current-change="load" />
    <p class="method-caption">删除后保留结果，可在回收站恢复。执行中的任务需先取消并等待结束。</p>
  </section>
  <div class="scope-note"><strong>任务执行说明</strong><p>等待 Worker 表示已经入队，尚未执行。Worker 需要独立启动；任务超过时限会明确失败。取消在阶段边界确认。这里只展示本人记录，文档解析任务由知识管理页面提交；普通解析可在失联后重领，视觉解析和知识向量任务不会自动重复云端调用。</p></div>
</template>
<style scoped>
.job-submit{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:22px}.job-submit .mono{margin-top:12px}.job-actions{display:flex;gap:10px;flex-wrap:wrap}.job-detail,.job-list{margin-top:22px}.job-detail p{font-size:12px;line-height:1.9}.job-detail .el-table{margin-top:15px}.job-list :deep(.el-table__row){cursor:pointer}@media(max-width:760px){.job-submit{display:block}.job-actions{margin-top:18px}}
</style>
