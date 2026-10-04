<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { api, ApiError, downloadExport } from '../api'
import { useAuthStore } from '../stores/auth'
import { errorText, timestamp } from '../format'
import type { ReportExport, MaintenanceTask } from '../types'
import ErrorPanel from './ErrorPanel.vue'

const props = defineProps<{ reportId: string }>(); const auth = useAuthStore()
const canManage = computed(() => auth.user?.permissions.includes('reports:review'))
const exports = ref<ReportExport[]>([]); const task = ref<MaintenanceTask | null>(null)
const error = ref(''); const busy = ref(false); const title = ref('模拟维护核对'); const note = ref('')
const target = ref<'in_progress' | 'completed' | 'cancelled'>('in_progress')
type ExportPending = { format: 'md' | 'json' | 'pdf'; idempotency_key: string }
type TaskPending = { operation: 'create' | 'update'; taskId?: string; body: { idempotency_key: string; title?: string; note: string; status?: 'in_progress' | 'completed' | 'cancelled'; expected_revision?: number } }
const exportPending = ref<ExportPending | null>(null); const taskPending = ref<TaskPending | null>(null)
const prefix = `machineguard.delivery.${auth.user?.id}.${props.reportId}`
const status: Record<string, string> = { queued: '等待导出', running: '正在导出', succeeded: '导出已保存', failed: '导出失败', cancelled: '已取消', pending: '待处理', in_progress: '处理中', completed: '已完成' }
let timer: number | undefined; let controller: AbortController | undefined; let disposed = false
async function load() {
  controller?.abort(); controller = new AbortController(); const signal = controller.signal; window.clearTimeout(timer)
  try {
    const [files, tasks] = await Promise.all([api.reportExports(auth.token, props.reportId, signal), api.maintenance(auth.token, props.reportId, signal)])
    if (signal.aborted) return
    exports.value = files.items; task.value = tasks.items[0] ?? null
    if (task.value?.status === 'in_progress' && target.value === 'in_progress') target.value = 'completed'
    if (!disposed && files.items.some(row => ['queued', 'running'].includes(row.job.status))) timer = window.setTimeout(load, 1500)
  } catch (failure) { if (!signal.aborted) error.value = errorText(failure) }
}
async function generate(format: ExportPending['format']) {
  if (busy.value) return
  busy.value = true; error.value = ''
  try {
    if (!exportPending.value) { exportPending.value = { format, idempotency_key: crypto.randomUUID() }; sessionStorage.setItem(`${prefix}.export`, JSON.stringify(exportPending.value)) }
    await api.createExport(auth.token, props.reportId, exportPending.value)
    sessionStorage.removeItem(`${prefix}.export`); exportPending.value = null; await load()
  } catch (failure) {
    error.value = errorText(failure)
    if (failure instanceof ApiError && failure.status >= 400 && failure.status < 500) { sessionStorage.removeItem(`${prefix}.export`); exportPending.value = null }
  } finally { busy.value = false }
}
async function download(row: ReportExport) {
  if (busy.value) return
  busy.value = true; error.value = ''
  try { await downloadExport(auth.token, props.reportId, row.export_id, row.format, row.sha256!) }
  catch (failure) { error.value = errorText(failure) } finally { busy.value = false }
}
async function cancel(row: ReportExport) {
  busy.value = true; error.value = ''
  try { await api.cancelJob(auth.token, row.job.job_id); await load() }
  catch (failure) { error.value = errorText(failure) } finally { busy.value = false }
}
async function saveTask() {
  if (busy.value || !taskPending.value && !note.value.trim()) return
  busy.value = true; error.value = ''
  try {
    if (!taskPending.value) {
      taskPending.value = task.value ? { operation: 'update', taskId: task.value.task_id, body: { idempotency_key: crypto.randomUUID(), expected_revision: task.value.revision, status: target.value, note: note.value.trim() } } : { operation: 'create', body: { idempotency_key: crypto.randomUUID(), title: title.value.trim(), note: note.value.trim() } }
      sessionStorage.setItem(`${prefix}.task`, JSON.stringify(taskPending.value))
    }
    const pending = taskPending.value
    if (pending.operation === 'create') await api.createMaintenance(auth.token, props.reportId, pending.body)
    else await api.updateMaintenance(auth.token, pending.taskId!, pending.body)
    sessionStorage.removeItem(`${prefix}.task`); taskPending.value = null; note.value = ''; await load()
  } catch (failure) {
    error.value = errorText(failure)
    if (failure instanceof ApiError && failure.status >= 400 && failure.status < 500) { sessionStorage.removeItem(`${prefix}.task`); taskPending.value = null; await load() }
  } finally { busy.value = false }
}
onMounted(() => {
  try {
    const savedExport = sessionStorage.getItem(`${prefix}.export`); const savedTask = sessionStorage.getItem(`${prefix}.task`)
    if (savedExport) exportPending.value = JSON.parse(savedExport) as ExportPending
    if (savedTask) taskPending.value = JSON.parse(savedTask) as TaskPending
  } catch { error.value = '原交付请求记录损坏，请核对任务与报告后处理。' }
  void load()
})
onBeforeUnmount(() => { disposed = true; controller?.abort(); window.clearTimeout(timer) })
</script>
<template>
  <div class="report-delivery">
    <details><summary>导出报告</summary>
      <p>包含完整报告字段、人工审核意见、引用版本与使用限制。</p>
      <div class="actions"><el-button v-if="exportPending" :loading="busy" @click="generate(exportPending.format)">确认原导出请求（{{ exportPending.format.toUpperCase() }}）</el-button><template v-else><el-button v-for="format in (['md', 'json', 'pdf'] as const)" :key="format" :disabled="busy" @click="generate(format)">{{ format === 'md' ? 'Markdown' : format.toUpperCase() }}</el-button></template><el-button text :disabled="busy" @click="load">刷新</el-button></div>
      <ul><li v-for="row in exports" :key="row.export_id"><span>{{ row.format.toUpperCase() }} · {{ status[row.job.status] }} · {{ timestamp(row.created_at) }}</span><el-button v-if="row.can_download" text :disabled="busy" @click="download(row)">下载</el-button><el-button v-if="['queued', 'running'].includes(row.job.status)" text :disabled="busy || !!row.job.cancel_requested_at" @click="cancel(row)">{{ row.job.cancel_requested_at ? '正在确认取消' : '取消导出' }}</el-button><p v-if="row.job.error">{{ row.job.error.code }}：{{ row.job.error.message }}</p><details v-if="row.sha256"><summary>文件核对信息</summary><p>{{ row.size }} 字节 · SHA-256：{{ row.sha256 }}</p></details></li></ul>
    </details>
    <details><summary>模拟维护任务{{ task ? ` · ${status[task.status]}` : '' }}</summary>
      <p>仅用于模拟设备维护流程，任务由人工创建和更新，不向设备下发操作。</p>
      <template v-if="task"><h3>{{ task.title }}</h3><p>状态：{{ status[task.status] }} · 版本 {{ task.revision }}</p><p>{{ task.note }}</p><details><summary>操作历史</summary><ol><li v-for="action in task.actions" :key="action.revision">{{ status[action.status] }} · {{ action.actor_name }} · {{ timestamp(action.created_at) }}<p>{{ action.note }}</p></li></ol></details></template>
      <form v-if="canManage && (!task || ['pending', 'in_progress'].includes(task.status) || taskPending)" @submit.prevent="saveTask">
        <label v-if="!task">任务名称<el-input v-model="title" maxlength="120" :disabled="busy || !!taskPending" /></label>
        <label v-if="task">更新为<el-select v-model="target" :disabled="busy || !!taskPending"><el-option v-if="task.status === 'pending'" label="处理中" value="in_progress" /><el-option v-if="task.status === 'in_progress'" label="已完成" value="completed" /><el-option label="已取消" value="cancelled" /></el-select></label>
        <label>操作说明（必填）<el-input v-model="note" type="textarea" maxlength="2000" :disabled="busy || !!taskPending" :rows="2" /></label>
        <el-button type="primary" native-type="submit" :loading="busy" :disabled="!taskPending && (!note.trim() || !title.trim())">{{ taskPending ? '确认原任务请求' : task ? '保存模拟任务更新' : '创建模拟维护任务' }}</el-button>
      </form>
    </details>
    <ErrorPanel :message="error" />
  </div>
</template>
<style scoped>
.report-delivery{border:1px solid #dce7dc;border-radius:8px;padding:10px 15px;margin:16px 0;background:#f8faf5;font-size:12px;overflow-wrap:anywhere}.report-delivery>details+details{border-top:1px solid #e1e9df;padding-top:12px;margin-top:12px}summary{cursor:pointer}p{line-height:1.8;margin:8px 0}.actions{display:flex;flex-wrap:wrap;gap:6px}.actions .el-button{margin:0}form{display:grid;gap:10px;margin-top:12px}label{display:grid;gap:6px}form>.el-button{justify-self:start}li{margin:10px 0}h3{font-size:14px}
</style>
