<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElCheckbox, ElInput } from 'element-plus'
import { api } from '../api'
import { useAuthStore } from '../stores/auth'
import { cancelled, errorText, timestamp } from '../format'
import type { DocumentParse, DocumentVersion, ManagementHistory, ManagementRequest, ParsedChunk, ParsedPage, ReviewRequest, SourceMetadata } from '../types'
import ErrorPanel from './ErrorPanel.vue'
import KnowledgePublishButton from './KnowledgePublishButton.vue'

const props = defineProps<{ version: DocumentVersion; embedded?: boolean; archived?: boolean }>()
const emit = defineEmits<{ changed: [] }>()
const auth = useAuthStore()
const open = ref(!!props.embedded); const loading = ref(false); const busy = ref(false); const error = ref(''); const notice = ref('')
const downloading = ref(false)
const state = ref<ManagementHistory | null>(null); const runs = ref<DocumentParse[]>([]); const runTotal = ref(0); const runPage = ref(1)
const selectedId = ref(''); const pageNumber = ref(1); const chunkNumber = ref(1); const historyPage = ref(1)
const pages = ref<ParsedPage[]>([]); const chunks = ref<ParsedChunk[]>([]); const pageTotal = ref(0); const chunkTotal = ref(0)
const contentReady = ref(false)
const selected = computed(() => runs.value.find(value => value.parse_id === selectedId.value))
const reviewLabels = { not_ready: '尚不可审核', pending: '待核对', approved: '发布前审核通过', rejected: '审核不通过', stale: '来源已纠正，需重新审核', inactive: '版本已停用' }
const actionLabels = { metadata: '纠正来源', deactivate: '停用版本', review: '解析审核' }
const runLabels = { waiting_review: '待人工审核', queued: '等待执行', running: '执行中', succeeded: '已完成', failed: '失败', cancelled: '已取消' }
const reason = ref(''); const checks = reactive({ source_checked: false, content_checked: false, location_checked: false })
const stopConfirmed = ref(false)
const edit = reactive<SourceMetadata>({ ...props.version.effective_source_metadata })
type Pending = { kind: 'metadata' | 'deactivate' | 'review'; target: string; body: ManagementRequest & Partial<ReviewRequest> & { metadata?: SourceMetadata } }
const storageKey = `machineguard.document-management.${auth.user?.id}.${props.version.version_id}`
const pending = ref<Pending | null>(null); const invalidSaved = ref(false)
const managementReady = ref(false)
const writable = computed(() => !props.archived && managementReady.value && !!state.value?.is_active && !loading.value && !busy.value && !pending.value && !invalidSaved.value)
let controller: AbortController | undefined; let contentController: AbortController | undefined; let disposed = false

try {
  const saved = sessionStorage.getItem(storageKey)
  if (saved) {
    const value = JSON.parse(saved)
    if (!['metadata', 'deactivate', 'review'].includes(value.kind) || typeof value.target !== 'string' ||
        typeof value.body !== 'object' || value.body === null || !/^[0-9a-f-]{36}$/i.test(value.body.idempotency_key)) throw new Error('管理请求记录非法。')
    pending.value = value
  }
} catch { invalidSaved.value = true }

async function loadContent(reset = false) {
  contentController?.abort(); contentController = new AbortController(); const signal = contentController.signal
  pages.value = []; chunks.value = []; pageTotal.value = 0; chunkTotal.value = 0
  contentReady.value = false
  if (reset) { pageNumber.value = 1; chunkNumber.value = 1; Object.assign(checks, { source_checked: false, content_checked: false, location_checked: false }) }
  if (!selected.value || selected.value.status !== 'succeeded') return
  loading.value = true; error.value = ''
  try {
    const id = selected.value.parse_id
    const [pageData, chunkData] = await Promise.all([api.parsePages(auth.token, id, (pageNumber.value - 1) * 4, signal), api.parseChunks(auth.token, id, (chunkNumber.value - 1) * 5, signal)])
    if (signal.aborted || disposed || selectedId.value !== id) return
    pages.value = pageData.items; pageTotal.value = pageData.total; chunks.value = chunkData.items; chunkTotal.value = chunkData.total
    contentReady.value = true
  } catch (failure) { if (!cancelled(failure)) error.value = errorText(failure) }
  finally { if (!signal.aborted) loading.value = false }
}
async function load() {
  controller?.abort(); contentController?.abort(); controller = new AbortController(); const signal = controller.signal
  loading.value = true; error.value = ''; managementReady.value = false
  try {
    const [history, parsed] = await Promise.all([api.management(auth.token, props.version.version_id, (historyPage.value - 1) * 10, signal), api.documentParses(auth.token, props.version.version_id, (runPage.value - 1) * 20, signal)])
    if (signal.aborted || disposed) return
    state.value = history; Object.assign(edit, history.effective_source_metadata)
    managementReady.value = true
    runs.value = parsed.items; runTotal.value = parsed.total
    if (!runs.value.some(value => value.parse_id === selectedId.value)) selectedId.value = runs.value[0]?.parse_id ?? ''
    await loadContent(true)
  } catch (failure) { if (!cancelled(failure)) error.value = errorText(failure) }
  finally { if (!signal.aborted) loading.value = false }
}
async function toggle() { open.value = !open.value; if (open.value) await load() }
async function downloadOriginal() {
  if (downloading.value) return
  downloading.value = true; error.value = ''
  try {
    const blob = await api.originalDocument(auth.token, props.version.version_id)
    const digest = await crypto.subtle.digest('SHA-256', await blob.arrayBuffer())
    const hash = Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, '0')).join('')
    if (blob.size !== props.version.size_bytes || hash !== props.version.sha256) throw new Error('原文字节数或 SHA-256 与版本记录不一致，不能下载。')
    if (disposed) return
    const address = URL.createObjectURL(blob); const link = document.createElement('a')
    link.href = address; link.download = props.version.filename; link.click()
    window.setTimeout(() => URL.revokeObjectURL(address), 1000)
    notice.value = '原文已核对字节数与 SHA-256，通过受控接口下载。'
  } catch (failure) { if (!disposed) error.value = errorText(failure) }
  finally { downloading.value = false }
}
function clearAttempt() { pending.value = null; invalidSaved.value = false; sessionStorage.removeItem(storageKey); notice.value = '已开始新的管理操作。' }
async function perform(attempt: Pending) {
  if (busy.value) return
  busy.value = true; error.value = ''; notice.value = ''
  try {
    // 请求前持久化动作键；响应丢失后直接确认原请求，不改用另一组审核依据。
    sessionStorage.setItem(storageKey, JSON.stringify(attempt)); pending.value = attempt
    const result = attempt.kind === 'metadata'
      ? await api.correctMetadata(auth.token, attempt.target, attempt.body as ManagementRequest & { metadata: SourceMetadata })
      : attempt.kind === 'deactivate' ? await api.deactivateVersion(auth.token, attempt.target, attempt.body)
        : await api.reviewParse(auth.token, attempt.target, attempt.body as ReviewRequest)
    if (disposed) return
    clearAttempt(); notice.value = result.replayed ? '已确认原管理记录，未重复写入。' : '管理结果已保存；发布依据变化后，需要重新核对并建立索引。'
    reason.value = ''; stopConfirmed.value = false; emit('changed'); await load()
  } catch (failure) { if (!disposed) error.value = errorText(failure) }
  finally { busy.value = false }
}
async function submit(kind: Pending['kind'], decision?: 'approved' | 'rejected') {
  if (!writable.value || !state.value) return
  if (!reason.value.trim()) { error.value = '请填写操作理由或审核意见。'; return }
  const body: Pending['body'] = { expected_revision: state.value.management_revision, idempotency_key: crypto.randomUUID(), reason: reason.value.trim() }
  let target = props.version.version_id
  if (kind === 'metadata') body.metadata = { ...edit, publication_date: edit.publication_date || null, source_url: edit.source_url || null }
  if (kind === 'deactivate' && !stopConfirmed.value) { error.value = '请先确认停用当前版本。'; return }
  if (kind === 'review') {
    if (!selected.value?.result_sha256 || selected.value.status !== 'succeeded' || !contentReady.value) { error.value = '请先读取成功保存的正文与切片，再进行审核。'; return }
    if (decision === 'approved' && !Object.values(checks).every(Boolean)) { error.value = '请完成三个核对项后再批准。'; return }
    target = selected.value.parse_id
    Object.assign(body, { decision, metadata_revision: state.value.metadata_revision, result_sha256: selected.value.result_sha256, ...checks })
  }
  await perform({ kind, target, body })
}
onMounted(() => { if (props.embedded) void load() })
onBeforeUnmount(() => { disposed = true; controller?.abort(); contentController?.abort() })
</script>

<template>
  <div v-if="!embedded" class="review-entry"><el-button :disabled="busy" @click="toggle">{{ open ? '收起预览与版本管理' : '解析预览与版本管理' }}</el-button><el-tag :type="version.is_active ? 'info' : 'danger'">{{ version.is_active ? '启用' : '已停用' }}</el-tag><span v-if="version.latest_parse">{{ reviewLabels[version.latest_parse.review_status] }}</span></div>
  <section v-if="open" class="review-panel" :aria-busy="loading || busy">
    <el-button :loading="downloading" :disabled="loading || busy || downloading" @click="downloadOriginal">下载原文核对</el-button>
    <ErrorPanel :message="error" /><p v-if="notice" role="status">{{ notice }}</p>
    <p v-if="loading">正在读取版本管理与解析内容。</p>
    <div v-if="pending || invalidSaved" class="scope-note"><p>{{ invalidSaved ? '本标签页的管理请求记录非法，请开始新操作。' : '存在待确认的管理请求；确认原请求可避免重复写入。' }}</p><el-button v-if="pending" :disabled="busy || loading" @click="perform(pending)">确认上次管理操作</el-button><el-button :disabled="busy" @click="clearAttempt">开始新的管理操作</el-button></div>
    <div v-if="state" class="review-content">
      <p v-if="archived || !state.is_active">{{ archived ? '资料已归档，仅供阅读；恢复后才能管理和发布。' : '此版本已停用。' }}</p>
      <div class="panel-heading"><h4>解析与发布状态</h4><el-button :disabled="busy || loading" @click="load">刷新状态</el-button></div>
      <el-select v-if="runs.length > 1" v-model="selectedId" aria-label="选择解析结果" :disabled="loading || busy" @change="loadContent(true)"><el-option v-for="run in runs" :key="run.parse_id" :value="run.parse_id" :label="`${run.mode === 'native' ? '普通' : '视觉'} · ${timestamp(run.created_at)} · ${runLabels[run.status]} · ${run.parse_id.slice(0, 8)}`" /></el-select>
      <el-pagination v-if="runTotal > 20" v-model:current-page="runPage" :page-size="20" :total="runTotal" layout="prev, pager, next" :disabled="loading || busy" @current-change="load" />
      <p v-if="!runs.length">当前版本尚无解析记录。</p>
      <template v-if="selected">
        <KnowledgePublishButton :key="selected.parse_id" :version-id="version.version_id" :parse-id="selected.parse_id" :revision="state.management_revision" :archived="archived" :published="selected.knowledge_status === 'published'" :approved="selected.review_status === 'approved'" :active="state.is_active && !loading && !busy && !pending" @changed="emit('changed'); load()" />
        <p>{{ reviewLabels[selected.review_status] }} · {{ selected.mode === 'native' ? '普通解析' : '视觉解析' }}</p>
        <ErrorPanel :message="selected.error ? `${selected.error.message}（${selected.error.code}）` : ''" />
        <p v-if="selected.status !== 'succeeded'">此解析尚无成功保存的正文与切片，不能审核。</p>
        <template v-else>
          <p class="method-caption">PDF 展示实际页码；Markdown/TXT 展示逻辑父页面。请结合原文核对正文、表格和位置。结构候选及警告均保留，不自动修正。字符范围为半开区间。</p>
          <details class="management-form"><summary>核对正文与页码（{{ pageTotal }} 页）</summary>
          <article v-for="item in pages" :key="item.page_id" class="preview-item"><strong>{{ version.file_format === 'pdf' ? '实际页' : '逻辑页' }} {{ item.page }}</strong><span> · 正文字符 {{ item.char_start }}–{{ item.char_end }}</span><pre class="source-text">{{ item.text }}</pre><p v-for="warning in item.extraction.warnings" :key="warning" class="warning-text">解析警告：{{ warning }}</p><details><summary>结构候选与提取信息</summary><div v-for="(block, blockIndex) in item.extraction.blocks" :key="blockIndex" class="structure-block"><template v-if="block.kind === 'table'"><strong>表格候选 {{ blockIndex + 1 }}</strong><p class="method-caption">按提取到的行列展示；空单元格以文字标记，未补齐或修改原结构。</p><div class="table-scroll"><table><tbody><tr v-for="(row, rowIndex) in block.rows" :key="rowIndex"><td v-for="(cell, cellIndex) in row" :key="cellIndex"><span v-if="cell === null || cell === ''" class="empty-cell">空单元格</span><span v-else>{{ cell }}</span></td></tr></tbody></table></div></template><p v-else>{{ block.kind === 'heading' ? '标题候选：' : '正文候选：' }}{{ block.text }}</p></div><details><summary>提取追溯详情</summary><pre>{{ JSON.stringify(item.extraction, null, 2) }}</pre></details></details></article>
          <el-pagination v-if="pageTotal > 4" class="page-pagination" aria-label="父页面分页" v-model:current-page="pageNumber" :page-size="4" :total="pageTotal" layout="prev, pager, next" :disabled="loading || busy" @current-change="loadContent()" />
          </details><details class="management-form"><summary>核对切片与出处（{{ chunkTotal }} 个）</summary>
          <article v-for="item in chunks" :key="item.chunk_id" class="preview-item"><strong>切片 {{ item.ordinal }} · {{ item.token_count }} tokens</strong><p>章节：{{ item.section || '未标注' }} · 起始页 {{ item.page }} · 正文字符 {{ item.char_start }}–{{ item.char_end }}</p><pre class="source-text">{{ item.text }}</pre><details><summary>页码映射与完整校验指纹</summary><pre>{{ JSON.stringify(item.page_spans, null, 2) }}</pre><p class="mono">{{ item.chunk_id }}<br />{{ item.sha256 }}</p></details></article>
          <el-pagination v-if="chunkTotal > 5" class="chunk-pagination" aria-label="切片分页" v-model:current-page="chunkNumber" :page-size="5" :total="chunkTotal" layout="prev, pager, next" :disabled="loading || busy" @current-change="loadContent()" />
          </details><details v-if="!archived && state.is_active" class="management-form" :open="selected.review_status !== 'approved'"><summary>{{ selected.review_status === 'approved' ? '重新审核（会改变发布依据）' : '审核后发布' }}</summary><p>核对下方三项并填写审核意见，再批准审核；之后点击“发布用于问答”。批准绑定当前来源修订和本次解析指纹，纠正来源后必须重新核对。</p><el-checkbox v-model="checks.source_checked" :disabled="!writable">已核对来源、许可和适用范围</el-checkbox><el-checkbox v-model="checks.content_checked" :disabled="!writable">已核对正文及表格内容</el-checkbox><el-checkbox v-model="checks.location_checked" :disabled="!writable">已核对页码、切片及出处位置</el-checkbox><label class="reason-label">操作理由／审核意见<el-input v-model="reason" aria-label="操作理由或审核意见" :disabled="!writable" type="textarea" maxlength="1000" :rows="3" /></label><div class="action-row"><el-button type="primary" :disabled="!writable || !reason.trim() || !contentReady || !Object.values(checks).every(Boolean)" @click="submit('review', 'approved')">批准发布前审核</el-button><el-button type="danger" plain :disabled="!writable" @click="submit('review', 'rejected')">审核不通过</el-button></div><p v-if="selected.review">最近意见：{{ selected.review.payload.reason }} · {{ selected.review.actor }} · {{ timestamp(selected.review.created_at) }}</p></details>
        </template>
      </template>
      <details class="management-form"><summary>纠正来源元数据</summary><p>上传时的信息与解析快照保留；本次纠正作为新来源修订保存。现有审核将失效。</p><div class="metadata-grid"><label>文档标题<el-input v-model="edit.title" :disabled="!writable" maxlength="200" /></label><label>作者或机构<el-input v-model="edit.author_or_organization" :disabled="!writable" maxlength="200" /></label><label>发布日期<el-input v-model="edit.publication_date" :disabled="!writable" placeholder="YYYY-MM-DD，未知留空" /></label><label>设备类型<el-input v-model="edit.equipment_type" :disabled="!writable" maxlength="200" /></label><label>适用范围<el-input v-model="edit.applicable_scope" :disabled="!writable" maxlength="1000" /></label><label>原始来源链接<el-input v-model="edit.source_url" :disabled="!writable" maxlength="2000" /></label><label>许可说明<el-input v-model="edit.license" :disabled="!writable" maxlength="200" /></label><label>来源类型<el-select v-model="edit.source_kind" :disabled="!writable"><el-option label="原始来源" value="original" /><el-option label="项目概括" value="project_summary" /><el-option label="模拟 SOP" value="simulated_sop" /></el-select></label></div><label>使用边界<el-input v-model="edit.usage_boundary" :disabled="!writable" type="textarea" maxlength="1000" /></label><el-checkbox v-model="edit.redistribution_allowed" :disabled="!writable">已核对许可允许公开再分发原文</el-checkbox><label class="reason-label">操作理由／审核意见<el-input v-model="reason" aria-label="操作理由或审核意见" :disabled="!writable" type="textarea" maxlength="1000" :rows="3" /></label><div class="action-row"><el-button :disabled="!writable" @click="submit('metadata')">保存来源纠正</el-button></div><details><summary>上传时的原始来源信息</summary><pre>{{ JSON.stringify(version.source_metadata, null, 2) }}</pre></details></details>
      <details class="management-form"><summary>停用此版本</summary><p>停用后禁止新增解析和审核，保留原文、解析结果与审计历史；停用版本立即退出新查询的证据范围，历史快照和出处仍保留。</p><el-checkbox v-model="stopConfirmed" :disabled="!writable">确认停用版本 {{ version.number }}</el-checkbox><label class="reason-label">操作理由／审核意见<el-input v-model="reason" aria-label="操作理由或审核意见" :disabled="!writable" type="textarea" maxlength="1000" :rows="3" /></label><div class="action-row"><el-button type="danger" plain :disabled="!writable || !stopConfirmed" @click="submit('deactivate')">保存版本停用</el-button></div></details>
      <details class="management-form"><summary>管理历史（{{ state.total }}）</summary><article v-for="item in state.items" :key="item.action_id" class="preview-item"><strong>{{ actionLabels[item.kind] }}{{ item.decision ? ` · ${item.decision === 'approved' ? '通过' : '不通过'}` : '' }}</strong><p>{{ item.actor }} · {{ timestamp(item.created_at) }} · 来源修订 {{ item.metadata_revision }}</p><p>{{ item.payload.reason }}</p><details><summary>该次操作依据与完整记录</summary><pre>{{ JSON.stringify(item, null, 2) }}</pre></details></article><el-pagination v-if="state.total > 10" v-model:current-page="historyPage" :page-size="10" :total="state.total" layout="prev, pager, next" :disabled="loading || busy" @current-change="load" /></details>
    </div>
  </section>
</template>

<style scoped>
.review-entry,.action-row{display:flex;flex-wrap:wrap;align-items:center;gap:12px;margin:16px 0}.review-panel{border:1px solid var(--el-border-color);border-radius:8px;padding:18px;margin:16px 0;min-width:0}.review-panel .el-select{width:100%}.preview-item{border-top:1px solid var(--el-border-color);padding:16px 0}.review-panel pre{white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;max-height:480px;overflow:auto;padding:12px;background:var(--el-fill-color-light);font-size:12px;line-height:1.8}.review-panel p{overflow-wrap:anywhere;line-height:1.8}.management-form{border-top:1px solid var(--el-border-color);padding:16px 0}.management-form summary{cursor:pointer;font-weight:600}.management-form .el-checkbox{display:flex;height:auto;white-space:normal;margin:12px 0}.metadata-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-bottom:12px}.metadata-grid label,.reason-label{display:block;font-size:13px;line-height:2}.reason-label{margin:16px 0}.warning-text{color:var(--el-color-warning-dark-2)}@media(max-width:760px){.metadata-grid{grid-template-columns:1fr}.review-panel{padding:12px}}
.structure-block{margin:16px 0}.table-scroll{max-width:100%;overflow:auto}.table-scroll table{border-collapse:collapse;width:100%;font-size:13px}.table-scroll td{border:1px solid var(--el-border-color);padding:8px;min-width:70px;overflow-wrap:anywhere}.empty-cell{color:var(--el-text-color-secondary);font-style:italic}
</style>
