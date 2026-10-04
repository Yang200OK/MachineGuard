<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import SkillsView from './SkillsView.vue'
import { ElCheckbox, ElForm, ElFormItem, ElInput, ElDrawer, ElDialog, ElMessageBox, ElTabs, ElTabPane } from 'element-plus'
import { api } from '../api'
import { useAuthStore } from '../stores/auth'
import { cancelled, errorText, timestamp } from '../format'
import type { DocumentDetail, DocumentSummary, DocumentVersion, SourceMetadata } from '../types'
import ErrorPanel from '../components/ErrorPanel.vue'
import KnowledgeReviewPanel from '../components/KnowledgeReviewPanel.vue'
import KnowledgeIndexPanel from '../components/KnowledgeIndexPanel.vue'
import CitationSource from '../components/CitationSource.vue'
import DocumentReader from '../components/DocumentReader.vue'
import type { CitationReference } from '../types'

const auth = useAuthStore(); const route = useRoute(); const router = useRouter()
const allowed = computed(() => auth.user?.permissions.includes('knowledge:manage'))
const documents = ref<DocumentSummary[]>([]); const current = ref<DocumentDetail | null>(null)
const tab = ref('library'); const search = ref(''); const category = ref('all'); const state = ref('published'); const archived = ref(false)
const categories = ['数据与设备', '预测与趋势', '模型说明', '知识助手与管理', '验收资料']
const detailOpen = ref(false); const detailTab = ref('正文'); const versionId = ref(''); const selectedVersion = ref<DocumentVersion | null>(null)
const total = ref(0); const page = ref(1); const versionPage = ref(1)
const loading = ref(false); const uploading = ref(false); const error = ref(''); const notice = ref('')
const file = ref<File | null>(null); const targetId = ref<string | null>(null)
const uploadOpen = ref(false)
const citationReference = computed<CitationReference | null>(() => {
  const { answer_id, claim_index, citation_index } = route.query
  if (typeof answer_id !== 'string' || !/^[0-9a-f-]{36}$/i.test(answer_id) || typeof claim_index !== 'string' || typeof citation_index !== 'string' || !/^\d+$/.test(claim_index) || !/^\d+$/.test(citation_index) || Number(claim_index) >= 6 || Number(citation_index) >= 5) return null
  return { answer_id, claim_index: Number(claim_index), citation_index: Number(citation_index) }
})
const displayVersions = computed(() => selectedVersion.value ? [selectedVersion.value] : [])
const readingParse = computed(() => documents.value.find(row => row.document_id === current.value?.document_id)?.published_version)
const form = reactive<SourceMetadata>({ title: '', author_or_organization: '', publication_date: null,
  equipment_type: '', applicable_scope: '', source_url: null, source_kind: 'original', license: '未知',
  usage_boundary: '仅供项目内部核对；许可未确认时不公开再分发原文。', redistribution_allowed: false })
const key = ref(''); const signature = ref(''); const storageKey = `machineguard.document-upload.${auth.user?.id}`
const invalidSavedAttempt = ref(false)
const parseLabel = { waiting_review: '待人工审核', queued: '等待 Worker', running: '解析中', succeeded: '解析完成', failed: '解析失败', cancelled: '已取消' }
const parsing = ref('')
let controller: AbortController | undefined; let disposed = false

async function load() {
  if (!allowed.value) return
  controller?.abort(); controller = new AbortController(); const signal = controller.signal
  loading.value = true; error.value = ''
  try {
    const data = await api.catalog(auth.token, (page.value - 1) * 10, search.value.trim(), category.value === 'all' ? '' : category.value, state.value === 'all' ? '' : state.value, archived.value, signal)
    if (signal.aborted) return
    documents.value = data.items; total.value = data.total
    const id = route.query.document_id
    if (typeof id === 'string') {
      // 同一文档刷新保留预览组件；新结果到达后整体替换，避免管理操作后丢失展开状态。
      if (current.value && current.value.document_id !== id) current.value = null
      const detail = await api.document(auth.token, id, (versionPage.value - 1) * 10, 10, signal)
      if (!signal.aborted) {
        current.value = detail; detailOpen.value = true
        const published = documents.value.find(row => row.document_id === id)?.published_version
        const selected = typeof route.query.version_id === 'string' ? route.query.version_id : selectedVersion.value?.document_id === id ? selectedVersion.value.version_id : published?.version_id ?? detail.versions.items[0]?.version_id
        if (selected) await chooseVersion(selected, signal)
      }
    } else { current.value = null; detailOpen.value = false; selectedVersion.value = null }
  } catch (failure) { if (!cancelled(failure)) error.value = errorText(failure) }
  finally { if (!signal.aborted) loading.value = false }
}
async function select(row: DocumentSummary) {
  if (uploading.value) return
  versionPage.value = 1
  detailTab.value = '正文'; selectedVersion.value = null; await router.replace({ query: { ...route.query, document_id: row.document_id, version_id: undefined, answer_id: undefined, claim_index: undefined, citation_index: undefined } })
}
async function manageDocument(documentId: string, id: string) {
  detailTab.value = '发布与管理'; selectedVersion.value = null
  await router.replace({ query: { document_id: documentId, version_id: id } })
}
async function chooseVersion(id: string, signal?: AbortSignal) {
  if (!current.value) return
  selectedVersion.value = null
  try { const value = await api.documentVersion(auth.token, current.value.document_id, id, signal); if (!signal?.aborted && !disposed) { selectedVersion.value = value; versionId.value = id } }
  catch (failure) { if (!cancelled(failure)) error.value = errorText(failure) }
}
async function closeDetails() {
  if (uploading.value || disposed || route.path !== '/knowledge') return
  const query = { ...route.query }; for (const key of ['document_id', 'version_id', 'answer_id', 'claim_index', 'citation_index']) delete query[key]
  controller?.abort(); loading.value = false; detailOpen.value = false; current.value = null; selectedVersion.value = null; await router.replace({ query })
}
async function setCategory(value: string) {
  if (!current.value) return
  try { await api.directory(auth.token, current.value.document_id, { category: value }); await load() } catch (failure) { error.value = errorText(failure) }
}
async function archive(row: DocumentSummary) {
  try { await ElMessageBox.confirm(row.archived_at ? `恢复“${row.title}”到资料库？有效审核与发布范围会重新核对。` : `归档“${row.title}”？它将不再作为新问答证据，旧回答原文仍可核对。`, '资料归档', { confirmButtonText: row.archived_at ? '恢复' : '归档', cancelButtonText: '取消' }); await api.directory(auth.token, row.document_id, { archived: !row.archived_at }); page.value = 1; await load() }
  catch (failure) { if (failure !== 'cancel' && failure !== 'close') error.value = errorText(failure) }
}
function filter() { page.value = 1; void load() }
function chooseFile(event: Event) {
  file.value = (event.target as HTMLInputElement).files?.[0] ?? null
}
function newAttempt(documentId: string | null) {
  if (uploading.value) return
  uploadOpen.value = true
  key.value = ''; signature.value = ''; targetId.value = documentId; notice.value = ''; error.value = ''
  invalidSavedAttempt.value = false
  sessionStorage.removeItem(storageKey)
  if (documentId && current.value?.versions.items[0]) Object.assign(form, current.value.versions.items[0].effective_source_metadata)
}
async function submit() {
  if (uploading.value) return
  error.value = ''; notice.value = ''
  if (invalidSavedAttempt.value) { error.value = '上传请求记录非法，请先点击“开始新上传”清除后再提交。'; return }
  if (!file.value) { error.value = '请选择 PDF、Markdown 或 TXT 原文。'; return }
  const selected = file.value
  if (!selected.size || selected.size > 10 * 1024 * 1024) { error.value = '文件必须非空且不超过 10 MiB。'; return }
  uploading.value = true
  try {
    const digest = await crypto.subtle.digest('SHA-256', await selected.arrayBuffer())
    const hash = Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, '0')).join('')
    const metadata: SourceMetadata = { ...form, publication_date: form.publication_date || null, source_url: form.source_url || null }
    const nextSignature = JSON.stringify({ target: targetId.value, filename: selected.name, sha256: hash, metadata })
    if (signature.value && signature.value !== nextSignature) throw new Error('原请求的文件或来源已改变。确认原上传时请选择原文件；另一项上传请先点击“开始新上传”。')
    if (!key.value) { key.value = crypto.randomUUID(); signature.value = nextSignature }
    // 在发请求前保存非敏感身份与元数据；刷新后重新选择原文件可确认同一上传。
    sessionStorage.setItem(storageKey, JSON.stringify({ key: key.value, signature: signature.value, target: targetId.value, metadata }))
    const body = new FormData(); body.append('file', selected); body.append('metadata', JSON.stringify(metadata)); body.append('idempotency_key', key.value)
    const result = await api.uploadDocument(auth.token, body, targetId.value)
    if (disposed) return
    notice.value = result.replayed || result.deduplicated ? '已确认原版本，未重复创建。' : '原文已保存到私有 OSS，来源与版本已入库。'
    versionPage.value = 1; uploadOpen.value = false
    sessionStorage.removeItem(storageKey); key.value = ''; signature.value = ''
    await router.replace({ query: { document_id: result.document_id } }); await load()
  } catch (failure) {
    if (!disposed) error.value = errorText(failure)
  } finally { uploading.value = false }
}
async function parse(version: DocumentVersion, mode: 'native' | 'vision', fresh = false) {
  if (parsing.value || uploading.value) return
  parsing.value = version.version_id; error.value = ''; notice.value = ''
  try {
    const savedKey = `machineguard.document-parse.${auth.user?.id}.${version.version_id}.${mode}`
    const prior = sessionStorage.getItem(savedKey)
    const requestKey = fresh || !prior ? crypto.randomUUID() : prior
    if (!/^[0-9a-f-]{36}$/i.test(requestKey)) throw new Error('解析请求记录非法，需要生成新的解析请求。')
    // 发请求前保存键；刷新和响应丢失后确认相同任务，不重复入队或视觉调用。
    sessionStorage.setItem(savedKey, requestKey)
    const result = await api.parseDocument(auth.token, version.version_id, mode, requestKey)
    if (disposed) return
    notice.value = result.replayed || result.deduplicated ? '已确认既有解析任务，未重复创建。' : '解析任务已提交，由独立 Worker 执行；完成后仍需核对，尚未发布。'
    await load()
  } catch (failure) { if (!disposed) error.value = errorText(failure) }
  finally { parsing.value = '' }
}
onMounted(async () => {
  const saved = sessionStorage.getItem(storageKey)
  if (saved) {
    try {
      const attempt = JSON.parse(saved)
      if (typeof attempt.key !== 'string' || typeof attempt.signature !== 'string' || !(attempt.target === null || typeof attempt.target === 'string') || typeof attempt.metadata !== 'object' || attempt.metadata === null) throw new Error('上传请求记录非法。')
      key.value = attempt.key; signature.value = attempt.signature; targetId.value = attempt.target; Object.assign(form, attempt.metadata)
      uploadOpen.value = true
      notice.value = '已恢复上传请求键。需要确认时重新选择原文件，页面状态以数据库为准。'
    } catch { invalidSavedAttempt.value = true }
  }
  await load()
  if (invalidSavedAttempt.value) error.value = '当前标签页的上传请求记录非法，请点击“开始新上传”清除后再提交。'
})
onBeforeUnmount(() => { disposed = true; controller?.abort() })
watch(() => [route.query.document_id, route.query.version_id, route.query.answer_id], () => { versionPage.value = 1; void load() })
</script>
<template>
  <div class="page-heading"><div><h1>知识与能力管理</h1><p>管理资料、发布范围，查看助手的分析能力。</p></div><div><el-button :loading="loading" @click="load">刷新</el-button><el-button v-if="allowed" type="primary" :disabled="uploading" @click="newAttempt(null)">上传资料</el-button></div></div>
  <ErrorPanel :message="error" /><div v-if="!allowed" class="scope-note">当前身份没有知识管理权限。</div>
  <template v-else><p v-if="notice" role="status">{{ notice }}</p><el-tabs v-model="tab"><el-tab-pane label="资料库" name="library"><section class="panel">
    <form class="catalog-filters" @submit.prevent="filter"><el-input v-model="search" aria-label="搜索资料" placeholder="搜索文档标题" maxlength="200" /><el-select v-model="category" aria-label="资料分类" @change="filter"><el-option label="全部分类" value="all" /><el-option v-for="name in categories" :key="name" :label="name" :value="name" /></el-select><el-select v-model="state" aria-label="资料状态" @change="filter"><el-option label="可用于问答" value="published" /><el-option label="全部状态" value="all" /><el-option v-for="name in ['待解析', '待审核', '待发布', '处理中', '失败', '已停用']" :key="name" :label="name" :value="name" /></el-select><el-button native-type="submit">搜索</el-button><el-button @click="archived = !archived; state = 'all'; filter()">{{ archived ? '返回资料库' : '已归档资料' }}</el-button></form>
    <p v-if="archived" class="method-caption">归档资料不会用于新问答，也不能直接发布。先恢复资料，再核对审核与发布状态。</p><div class="panel-heading"><h2>{{ archived ? '已归档资料' : '知识文档' }}</h2><span>{{ total }} 份</span></div><el-table :data="documents" @row-click="select"><el-table-column label="文档" min-width="220"><template #default="scope"><strong>{{ scope.row.title }}</strong><p class="catalog-description">{{ scope.row.description }}</p><small>{{ scope.row.category }}</small></template></el-table-column><el-table-column label="问答使用版本" width="130"><template #default="scope">{{ scope.row.published_version ? `版本 ${scope.row.published_version.version_number}` : '尚未用于问答' }}</template></el-table-column><el-table-column label="最新上传" width="130"><template #default="scope">版本 {{ scope.row.latest_version.number }}<p v-if="!scope.row.archived_at && (!scope.row.published_version || scope.row.latest_version.version_id !== scope.row.published_version.version_id)" class="catalog-description">{{ scope.row.stage }}</p></template></el-table-column><el-table-column label="状态" width="125"><template #default="scope"><span class="knowledge-status" :class="{ 'is-available': scope.row.available }">{{ scope.row.archived_at ? '已归档' : scope.row.available ? '可用于问答' : scope.row.stage }}</span></template></el-table-column><el-table-column label="操作" width="150"><template #default="scope"><el-button text @click.stop="select(scope.row)">阅读</el-button><el-button text @click.stop="archive(scope.row)">{{ scope.row.archived_at ? '恢复' : '归档' }}</el-button></template></el-table-column></el-table>
    <p v-if="!loading && !documents.length" class="method-caption">此范围没有资料，可以切换“全部状态”或上传新资料。</p><el-pagination v-if="total > 10" v-model:current-page="page" :page-size="10" :total="total" layout="prev, pager, next" @current-change="load" /></section></el-tab-pane><el-tab-pane label="发布管理" name="publication"><KnowledgeIndexPanel v-if="tab === 'publication'" @manage="manageDocument" /></el-tab-pane><el-tab-pane label="分析能力" name="capabilities"><SkillsView v-if="tab === 'capabilities'" embedded /></el-tab-pane></el-tabs>
    <el-drawer :model-value="detailOpen" @update:model-value="value => { if (!value) void closeDetails() }" title="阅读与管理资料" size="min(780px, 100vw)" :close-on-click-modal="false" :show-close="!uploading" :close-on-press-escape="!uploading">
      <div v-if="current" class="document-detail"><div class="panel-heading"><h2>{{ selectedVersion?.effective_source_metadata.title || current.title }}</h2><el-button :disabled="uploading" @click="closeDetails">关闭资料</el-button></div>
      <ErrorPanel :message="error" /><div class="version-selector"><label>选择版本<el-select v-model="versionId" aria-label="文档版本" @change="chooseVersion($event)"><el-option v-if="selectedVersion && !current.versions.items.some(item => item.version_id === selectedVersion!.version_id)" :value="selectedVersion.version_id" :label="`版本 ${selectedVersion.number} · 回答使用版本`" /><el-option v-for="item in current.versions.items" :key="item.version_id" :value="item.version_id" :label="`版本 ${item.number} · ${item.filename}`" /></el-select></label><el-pagination v-if="current.versions.total > 10" v-model:current-page="versionPage" :page-size="10" :total="current.versions.total" layout="prev, next" @current-change="load" /></div>
      <div v-if="citationReference" class="cited-source-panel"><div class="panel-heading"><strong>回答当时引用的原文</strong><router-link :to="route.query.conversation_id ? { path: '/assistant', query: { conversation_id: route.query.conversation_id, turn_id: citationReference.answer_id } } : { path: '/assistant', query: { legacy_answer_id: citationReference.answer_id } }">返回原会话</router-link></div><CitationSource :reference="citationReference" /></div><ErrorPanel v-else-if="route.query.answer_id" message="引用定位参数不完整，请从回答引用重新打开。" />
      <el-tabs v-model="detailTab"><el-tab-pane v-for="name in ['正文', '来源与许可', '发布与管理', '技术详情']" :key="name" :name="name" :label="name" /></el-tabs>
      <article v-for="version in displayVersions" :key="version.version_id"><DocumentReader v-if="detailTab === '正文'" :version="version" :parse-id="readingParse?.version_id === version.version_id ? readingParse.parse_id : undefined" />
      <div v-if="detailTab === '来源与许可'"><label>资料分类<el-select :model-value="current.category" aria-label="修改资料分类" @change="setCategory"><el-option v-for="name in categories" :key="name" :label="name" :value="name" /></el-select></label><dl class="detail-grid"><dt>作者或机构</dt><dd>{{ version.effective_source_metadata.author_or_organization }}</dd><dt>发布日期</dt><dd>{{ version.effective_source_metadata.publication_date || '未知' }}</dd><dt>适用范围</dt><dd>{{ version.effective_source_metadata.applicable_scope }}</dd><dt>来源链接</dt><dd><a v-if="version.effective_source_metadata.source_url" :href="version.effective_source_metadata.source_url" target="_blank" rel="noopener noreferrer">查看来源</a><span v-else>未提供</span></dd><dt>许可</dt><dd>{{ version.effective_source_metadata.license }}</dd><dt>使用边界</dt><dd>{{ version.effective_source_metadata.usage_boundary }}</dd></dl></div>
      <div v-if="detailTab === '发布与管理'"><p class="method-caption">解析正文 → 核对审核 → 发布用于问答。完成的步骤无需重复操作。</p><el-button v-if="!current.archived_at" :disabled="uploading" @click="newAttempt(current.document_id)">上传此文档的新版本</el-button>
        <ErrorPanel :message="version.error ? `${version.error.message}（${version.error.code}）` : ''" />
        <div v-if="!current.archived_at && version.status === 'uploaded' && version.is_active && version.parse_status !== 'succeeded'" class="parse-actions">
          <el-button :disabled="!!parsing || uploading || ['queued', 'running'].includes(version.parse_status)" @click="parse(version, 'native')">解析正文或确认原任务</el-button>
          <el-button v-if="version.file_format === 'pdf'" :disabled="!!parsing || uploading || ['queued', 'running'].includes(version.parse_status)" @click="parse(version, 'vision')">视觉解析（百炼用量）</el-button>
          <el-button v-if="['failed', 'cancelled'].includes(version.parse_status)" :disabled="!!parsing || uploading" @click="parse(version, 'native', true)">新建普通解析</el-button>
          <el-button v-if="version.file_format === 'pdf' && ['failed', 'cancelled'].includes(version.parse_status)" :disabled="!!parsing || uploading" @click="parse(version, 'vision', true)">新建视觉解析</el-button>
        </div>
        <KnowledgeReviewPanel v-if="version.status === 'uploaded'" :version="version" embedded :archived="!!current.archived_at" @changed="load" />
        <details v-if="version.latest_parse" class="parse-status"><summary>解析技术记录</summary>
          <p><strong>{{ parseLabel[version.latest_parse.status] }}</strong> · {{ version.latest_parse.mode === 'native' ? '普通解析' : '视觉解析' }} · {{ version.latest_parse.progress }}%</p>
          <p class="mono">解析编号：{{ version.latest_parse.parse_id }}<br />任务编号：{{ version.latest_parse.job_id }}</p>
          <p v-if="version.latest_parse.status === 'succeeded'">父页面 {{ version.latest_parse.page_count }}，切片 {{ version.latest_parse.chunk_count }}；{{ version.latest_parse.tokenizer }}：上限 {{ version.latest_parse.chunk_tokens }} tokens，重叠目标 {{ version.latest_parse.overlap_tokens }}。解析完成不代表内容已核对或可检索。</p>
          <p v-if="version.latest_parse.model">模型：{{ version.latest_parse.model }}</p>
          <ErrorPanel :message="version.latest_parse.error ? `${version.latest_parse.error.message}（${version.latest_parse.error.code}）` : ''" />
        </details>
      </div><div v-if="detailTab === '技术详情'">
        <dl class="detail-grid"><dt>版本编号</dt><dd class="mono">{{ version.version_id }}</dd><dt>SHA-256</dt><dd class="mono">{{ version.sha256 }}</dd><dt>文件大小</dt><dd>{{ version.size_bytes }} 字节</dd><dt>上传尝试</dt><dd>{{ version.attempt_count }} / {{ version.max_attempts }}</dd><dt>提交时间</dt><dd>{{ timestamp(version.created_at) }}</dd><dt>原文保存时间</dt><dd>{{ version.uploaded_at ? timestamp(version.uploaded_at) : '尚未确认' }}</dd><dt>来源标题</dt><dd>{{ version.effective_source_metadata.title }}</dd><dt>作者或机构</dt><dd>{{ version.effective_source_metadata.author_or_organization }}</dd><dt>发布日期</dt><dd>{{ version.effective_source_metadata.publication_date || '未知' }}</dd><dt>设备与范围</dt><dd>{{ version.effective_source_metadata.equipment_type }} · {{ version.effective_source_metadata.applicable_scope }}</dd><dt>来源链接</dt><dd><a v-if="version.effective_source_metadata.source_url" :href="version.effective_source_metadata.source_url" target="_blank" rel="noopener noreferrer">{{ version.effective_source_metadata.source_url }}</a><span v-else>未提供</span></dd><dt>许可与边界</dt><dd>{{ version.effective_source_metadata.license }} · {{ version.effective_source_metadata.usage_boundary }}</dd></dl>
      </div></article></div>
    </el-drawer>
<el-dialog v-model="uploadOpen" title="上传资料" width="min(760px, 96vw)" :close-on-click-modal="false" :show-close="!uploading" :close-on-press-escape="!uploading">
<p class="method-caption">保存原文 → 解析正文 → 核对与审核 → 发布用于问答</p>
    <section class="panel">
      <div class="panel-heading"><h2>{{ targetId ? '上传该文档的新版本' : '上传新文档' }}</h2><el-button :disabled="uploading" @click="newAttempt(null)">开始新上传</el-button></div>
      <p v-if="targetId" class="mono">文档编号：{{ targetId }}</p>
      <el-form label-position="top" :disabled="uploading" @submit.prevent="submit">
        <div class="source-form">
          <el-form-item label="文档标题"><el-input v-model="form.title" maxlength="200" /></el-form-item>
          <el-form-item label="作者或机构"><el-input v-model="form.author_or_organization" maxlength="200" /></el-form-item>
          <el-form-item label="发布日期（未知可留空）"><el-input v-model="form.publication_date" placeholder="YYYY-MM-DD" /></el-form-item>
          <el-form-item label="适用设备类型"><el-input v-model="form.equipment_type" maxlength="200" /></el-form-item>
          <el-form-item label="来源类型"><el-select v-model="form.source_kind"><el-option label="原始来源" value="original" /><el-option label="项目概括" value="project_summary" /><el-option label="模拟 SOP" value="simulated_sop" /></el-select></el-form-item>
          <el-form-item label="许可说明"><el-input v-model="form.license" maxlength="200" /></el-form-item>
          <el-form-item label="原始来源链接（可留空）"><el-input v-model="form.source_url" maxlength="2000" /></el-form-item>
          <el-form-item label="适用范围"><el-input v-model="form.applicable_scope" maxlength="1000" /></el-form-item>
        </div>
        <el-form-item label="使用边界"><el-input v-model="form.usage_boundary" type="textarea" maxlength="1000" :rows="2" /></el-form-item>
        <el-form-item><el-checkbox v-model="form.redistribution_allowed">已核对许可允许公开再分发原文</el-checkbox></el-form-item>
        <label class="file-label" for="document-file">原始文件（PDF / .md / .txt，最大 10 MiB）</label>
        <input id="document-file" type="file" accept=".pdf,.md,.txt" :disabled="uploading" @change="chooseFile" />
        <div class="upload-actions"><el-button type="primary" :loading="uploading" native-type="submit">{{ key ? '确认同一上传' : '保存原文与版本' }}</el-button><span v-if="key" class="mono">请求键：{{ key }}</span></div>
      </el-form>
    </section>
    </el-dialog>
  </template>
</template>

<style scoped>
.upload-panel>summary{cursor:pointer;font-weight:600}.upload-panel>section{border:0;box-shadow:none;padding:12px 0 0}.cited-version{border:1px solid #9cbd89;border-radius:8px;padding:18px}.parse-actions{display:flex;flex-wrap:wrap;gap:10px;margin:16px 0}.parse-status{padding:14px;background:var(--el-fill-color-light);margin-bottom:20px}.parse-status p{overflow-wrap:anywhere;font-size:12px;line-height:1.9}
.panel{margin-top:22px}.panel-heading{flex-wrap:wrap}.source-form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 22px}.file-label{display:block;margin-bottom:10px;color:var(--el-text-color-regular)}input[type=file]{max-width:100%}.upload-actions{display:flex;align-items:center;flex-wrap:wrap;gap:16px;margin-top:22px}.upload-actions span,.version-card dd{overflow-wrap:anywhere}.version-card{border-top:1px solid var(--el-border-color);padding-top:22px;margin-top:22px}.version-card .detail-grid{margin-bottom:22px}.el-select{width:100%}@media(max-width:760px){.source-form{grid-template-columns:1fr}.detail-grid{grid-template-columns:1fr;gap:4px}.detail-grid dd{margin-bottom:12px}}
</style>

<style scoped>
.knowledge-status{display:inline-block;padding:3px 8px;border:1px solid #dce4d7;border-radius:5px;background:#f3f5f0;color:#5b6e54;white-space:nowrap;font-size:11px}.knowledge-status.is-available{background:#edf5e8;border-color:#d5e5ca;color:#486c36}.catalog-filters{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:22px}.catalog-filters>.el-input{width:240px}.catalog-filters>.el-select{width:145px}.catalog-description{font-size:11px;color:#83917c;line-height:1.7;max-width:460px;margin:7px 0}.version-selector{margin:20px 0}.version-selector label{display:grid;gap:8px}.document-detail{color:#3c5d47}.document-detail h2{font-size:19px}.document-detail .detail-grid{font-size:13px}.document-detail article{padding:12px 0}.cited-source-panel{border:1px solid #dbe7d2;padding:18px;border-radius:8px;margin:18px 0}.document-detail .parse-status{margin-top:18px}.document-detail .el-tabs{margin-top:20px}@media(max-width:760px){.catalog-filters>.el-input{width:100%}.catalog-filters>.el-select{width:calc(50% - 5px)}}
</style>
