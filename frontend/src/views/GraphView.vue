<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElInput, ElDrawer, ElCheckbox, ElTabs, ElTabPane, ElEmpty } from 'element-plus'
import { api, ApiError } from '../api'
import { useAuthStore } from '../stores/auth'
import { cancelled, errorText, timestamp } from '../format'
import type { CitationSource } from '../types'
import type { EntityKind, GraphCreateRequest, GraphDetail, GraphEntity, GraphRelation, GraphReviewRequest, GraphSource, GraphStatus } from '../graphTypes'
import ErrorPanel from '../components/ErrorPanel.vue'
import SourceGraph from '../components/SourceGraph.vue'
import GraphPathSearch from '../components/GraphPathSearch.vue'

const auth = useAuthStore(); const route = useRoute(); const router = useRouter()
const allowed = computed(() => auth.user?.permissions.includes('knowledge:manage'))
const kinds: EntityKind[] = ['设备类型', '组件', '现象', '概念', '检查步骤', '规则', '单位', '文档', '章节']
const labels: Record<GraphStatus, string> = { pending: '待审核', approved: '已批准', rejected: '已拒绝', withdrawn: '已撤回' }
const tab = ref('usable'); const page = ref(1); const total = ref(0); const items = ref<GraphRelation[]>([])
const pathsOpen = ref(false); const pathEntity = ref<GraphEntity>(); const listOpen = ref(false)
function openPaths(entity?: GraphEntity) { pathEntity.value = entity ? { ...entity } : undefined; pathsOpen.value = true }
const error = ref(''); const loading = ref(false); const notice = ref('')
const opened = ref(false); const detail = ref<GraphDetail>(); const source = ref<CitationSource>()
const detailError = ref(''); const detailLoading = ref(false)
const reason = ref(''); const evidenceChecked = ref(false); const directionChecked = ref(false)
const createOpen = ref(false); const sources = ref<GraphSource[]>([]); const sourcePage = ref(1); const sourceTotal = ref(0)
const sourceLoading = ref(false); const createError = ref(''); const selected = ref<GraphSource>(); const selectedId = ref('')
const original = ref<HTMLTextAreaElement>(); const quoteStart = ref(0); const quoteEnd = ref(0)
const draft = reactive({ subject: '', subject_kind: '概念' as EntityKind, object: '', object_kind: '概念' as EntityKind, predicate: '', note: '' })
const quote = computed(() => selected.value ? [...selected.value.text].slice(quoteStart.value, quoteEnd.value).join('') : '')
const busy = ref(false)
type Pending = { kind: 'create'; body: GraphCreateRequest } | { kind: 'review'; id: string; body: GraphReviewRequest }
const pending = ref<Pending>()
const storageKey = `machineguard.graph.${auth.user?.id}.pending`
// 只保存待确认的业务参数，超时或刷新后使用原动作键确认，绝不另建请求。
const saved = sessionStorage.getItem(storageKey)
if (saved) { try { pending.value = JSON.parse(saved) as Pending } catch { error.value = '原图谱请求无法读取，请保留当前标签页并核对后台记录。' } }
let listController: AbortController | undefined; let detailController: AbortController | undefined; let sourceController: AbortController | undefined
function retain(value?: Pending) { pending.value = value; if (value) sessionStorage.setItem(storageKey, JSON.stringify(value)); else sessionStorage.removeItem(storageKey) }
async function load() {
  if (!allowed.value) return
  listController?.abort(); listController = new AbortController(); const signal = listController.signal
  loading.value = true; error.value = ''; items.value = []
  try { const result = await api.graphRelations(auth.token, tab.value === 'usable' ? 'approved' : tab.value, tab.value === 'usable', (page.value - 1) * 20, signal)
    if (!signal.aborted) { items.value = result.items; total.value = result.total } }
  catch (failure) { if (!cancelled(failure)) error.value = errorText(failure) }
  finally { if (!signal.aborted) loading.value = false }
}
async function inspect(id: string) {
  detailController?.abort(); detailController = new AbortController(); const signal = detailController.signal
  opened.value = true; detail.value = undefined; source.value = undefined; detailError.value = ''; detailLoading.value = true
  reason.value = ''; evidenceChecked.value = false; directionChecked.value = false
  await router.replace({ path: '/graph', query: { relation_id: id } })
  try { const value = await api.graphRelation(auth.token, id, signal); const originalSource = await api.graphSource(auth.token, id, signal)
    if (!signal.aborted) { detail.value = value; source.value = originalSource } }
  catch (failure) { if (!cancelled(failure)) detailError.value = errorText(failure) }
  finally { if (!signal.aborted) detailLoading.value = false }
}
function closeDetail() { detailController?.abort(); detail.value = undefined; source.value = undefined; void router.replace('/graph') }
async function loadSources() {
  sourceController?.abort(); sourceController = new AbortController(); const signal = sourceController.signal
  sourceLoading.value = true; createError.value = ''; selected.value = undefined; selectedId.value = ''; sources.value = []
  try { const result = await api.graphSources(auth.token, (sourcePage.value - 1) * 10, signal)
    if (!signal.aborted) { sources.value = result.items; sourceTotal.value = result.total } }
  catch (failure) { if (!cancelled(failure)) createError.value = errorText(failure) }
  finally { if (!signal.aborted) sourceLoading.value = false }
}
function chooseSource(id: string) {
  selected.value = sources.value.find(item => item.chunk_id === id)
  quoteStart.value = 0; quoteEnd.value = Math.min(1200, [...(selected.value?.text ?? '')].length)
  Object.assign(draft, { subject: '', subject_kind: '概念', object: '', object_kind: '概念', predicate: '', note: '' })
}
function useSelection() {
  if (!original.value || !selected.value) return
  const start = original.value.selectionStart; const end = original.value.selectionEnd
  if (start === end) { createError.value = '请先在原文中选中一段连续文字。'; return }
  // 浏览器位置为 UTF-16，转换成服务端采用的 Unicode 字符位置，避免跨字节字符错位。
  quoteStart.value = [...selected.value.text.slice(0, start)].length; quoteEnd.value = [...selected.value.text.slice(0, end)].length
  createError.value = ''
}
async function mutate(value: Pending) {
  if (busy.value) return
  busy.value = true; error.value = ''; createError.value = ''; detailError.value = ''; retain(value)
  try {
    const result = value.kind === 'create' ? await api.createGraphRelation(auth.token, value.body) : await api.reviewGraphRelation(auth.token, value.id, value.body)
    retain(); createOpen.value = false; notice.value = result.deduplicated ? '已有相同原文关系，已打开原记录。' : result.status === 'pending' ? '候选已保存，审核通过后才进入可用图谱。' : `关系已${labels[result.status].replace('已', '')}，审核记录已保存。`
    await load(); await inspect(result.relation_id)
  } catch (failure) {
    // 明确的参数或修订拒绝表示本次未写入；未知结果继续保留原请求供确认。
    if (failure instanceof ApiError && [400, 401, 403, 404, 409, 422].includes(failure.status)) retain()
    const message = errorText(failure)
    if (value.kind === 'create') createError.value = message; else detailError.value = message
    error.value = message
  } finally { busy.value = false }
}
function submitCreate() {
  const item = selected.value
  if (!item || pending.value || !quote.value || !draft.subject.trim() || !draft.object.trim() || !draft.predicate.trim() || !draft.note.trim()) return
  void mutate({ kind: 'create', body: { ...draft, idempotency_key: crypto.randomUUID(), snapshot_id: item.snapshot_id,
    chunk_id: item.chunk_id, chunk_sha256: item.chunk_sha256, quote_start: quoteStart.value, quote_end: quoteEnd.value } })
}
function submitReview(decision: GraphReviewRequest['decision']) {
  if (!detail.value || !reason.value.trim() || pending.value || busy.value) return
  void mutate({ kind: 'review', id: detail.value.relation_id, body: { idempotency_key: crypto.randomUUID(), expected_revision: detail.value.revision,
    decision, reason: reason.value, evidence_checked: evidenceChecked.value, direction_checked: directionChecked.value } })
}
watch(tab, () => { page.value = 1; void load() })
watch(page, () => { void load() })
watch(sourcePage, () => { void loadSources() })
onMounted(async () => { await load(); if (allowed.value && typeof route.query.relation_id === 'string') await inspect(route.query.relation_id) })
onBeforeUnmount(() => { listController?.abort(); detailController?.abort(); sourceController?.abort() })
</script>

<template>
  <div class="page-heading graph-heading"><div><div class="eyebrow">KNOWLEDGE GRAPH</div><h1>知识图谱</h1><p>把资料里的概念与关系保存为可核对的原文依据。</p></div>
    <div v-if="allowed" class="graph-buttons"><el-button :loading="loading" @click="load">刷新</el-button><el-button type="primary" :disabled="busy || !!pending" @click="createOpen = true; sourcePage = 1; loadSources()">从原文建立关系</el-button></div>
  </div>
  <div v-if="!allowed" class="scope-note">当前身份没有知识管理权限，无法查看或审核资料图谱。</div>
  <template v-else>
    <ErrorPanel :message="error" retry @retry="load" /><p v-if="notice" class="graph-notice" role="status">{{ notice }}</p>
    <div v-if="pending" class="scope-note"><strong>上次图谱请求的结果尚未确认</strong><p>使用原请求键核对同一次操作。</p><el-button :loading="busy" @click="mutate(pending)">确认原图谱请求</el-button></div>
    <el-tabs v-model="tab"><el-tab-pane label="已审核图谱" name="usable" /><el-tab-pane label="待审核" name="pending" /><el-tab-pane label="全部与历史" name="all" /></el-tabs>
    <section v-if="tab === 'usable' && items.length" class="panel graph-panel"><h2>本页有效关系</h2><p class="method-caption">点击实体查询两跳内路径，点击连线查看原文。仅显示已批准且来源有效的关系。</p><SourceGraph :relations="items" @inspect="inspect" @paths="openPaths" /><div class="graph-buttons"><el-button @click="openPaths()">按名称查询路径</el-button><el-button @click="listOpen = !listOpen">{{ listOpen ? '收起关系清单' : '查看关系清单' }}</el-button></div></section>
    <div v-if="tab !== 'usable' || listOpen || !items.length" v-loading="loading" class="graph-list">
      <el-empty v-if="!loading && !items.length && !error" :description="tab === 'usable' ? '暂无已批准且来源有效的关系' : '当前分组没有关系记录'" />
      <section v-for="item in items" :key="item.relation_id" class="panel graph-card">
        <div class="graph-card-top"><h2>{{ item.subject.name }} <span class="relation-predicate">— {{ item.predicate }} →</span> {{ item.object.name }}</h2>
          <el-tag :type="item.usable ? 'success' : item.status === 'pending' ? 'warning' : 'info'">{{ labels[item.status] }}</el-tag></div>
        <p class="method-caption">{{ item.subject.document_title }} · 版本 {{ item.citation.document_version }} · {{ item.citation.section }} · 第 {{ item.citation.page_spans.map(span => span.page).join('、') }} 页</p>
        <blockquote>{{ item.citation.quote }}</blockquote><p v-if="!item.source_available" class="graph-warning">{{ item.unavailable_reason }} 历史出处仍保留。</p>
        <el-button @click="inspect(item.relation_id)">核对原文与审核记录</el-button>
      </section>
    </div>
    <el-pagination v-if="total > 20" v-model:current-page="page" :page-size="20" :total="total" layout="prev, pager, next" />
  </template>
  <el-drawer v-model="opened" title="原文与关系审核" size="min(720px, 100vw)" @closed="closeDetail">
    <ErrorPanel :message="detailError" /><div v-loading="detailLoading" class="graph-detail">
      <template v-if="detail"><h2>{{ detail.subject.name }} — {{ detail.predicate }} → {{ detail.object.name }}</h2>
        <p class="method-caption">{{ labels[detail.status] }} · 修订 {{ detail.revision }} · {{ detail.subject.kind }} / {{ detail.object.kind }}</p>
        <p>摘录说明：{{ detail.note }}</p><p class="method-caption">命名空间：{{ detail.subject.document_title }}（不同文档的同名实体分别保存）</p>
        <p v-if="!detail.source_available" class="graph-warning">{{ detail.unavailable_reason }}</p>
        <template v-if="source"><h3>固定版本原文</h3><p>{{ source.citation.source_metadata.title }} · 版本 {{ source.citation.document_version }} · {{ source.filename }}</p>
          <p class="method-caption">{{ source.citation.source_metadata.applicable_scope }}</p>
          <div v-for="(context, index) in source.contexts" :key="index" class="graph-original"><strong>第 {{ context.page }} 页</strong><p>{{ context.before }}<mark>{{ context.quote }}</mark>{{ context.after }}</p></div>
        </template>
        <div v-if="detail.status === 'pending' || detail.status === 'approved'" class="graph-review">
          <h3>{{ detail.status === 'pending' ? '审核候选' : '撤回已批准关系' }}</h3>
          <label>审核意见<el-input v-model="reason" type="textarea" :rows="3" maxlength="1000" show-word-limit :disabled="busy || !!pending" aria-label="图谱审核意见" /></label>
          <template v-if="detail.status === 'pending'"><el-checkbox v-model="evidenceChecked" :disabled="busy || !!pending">原文支持所填写的实体和关系</el-checkbox><el-checkbox v-model="directionChecked" :disabled="busy || !!pending">实体类型与关系方向已核对</el-checkbox></template>
          <div class="graph-buttons"><template v-if="detail.status === 'pending'"><el-button type="primary" :disabled="busy || !!pending || !reason.trim() || !evidenceChecked || !directionChecked || !detail.source_available || !source" @click="submitReview('approved')">批准关系</el-button><el-button :disabled="busy || !!pending || !reason.trim()" @click="submitReview('rejected')">拒绝关系</el-button></template>
            <el-button v-else :disabled="busy || !!pending || !reason.trim()" @click="submitReview('withdrawn')">撤回关系</el-button></div>
        </div>
        <el-collapse><el-collapse-item title="查看完整审核历史与来源身份" name="history"><div v-for="action in detail.actions" :key="action.action_id" class="graph-action"><strong>{{ action.kind === 'create' ? '建立候选' : labels[action.result.status as GraphStatus] }} · {{ action.actor }}</strong><p>{{ action.payload.reason ?? action.payload.note }}</p><p class="method-caption">{{ timestamp(action.created_at) }} · 修订 {{ action.revision }}</p></div><p class="mono">关系 {{ detail.relation_id }}<br>快照 {{ detail.snapshot_id }}<br>切片 {{ detail.citation.chunk_id }}<br>命名空间 {{ detail.subject.namespace }}</p></el-collapse-item></el-collapse>
      </template>
    </div>
  </el-drawer>
  <el-drawer v-model="pathsOpen" title="查询证据路径" size="min(720px, 100vw)" destroy-on-close><GraphPathSearch v-if="pathsOpen" :entity="pathEntity" @inspect="inspect" /></el-drawer>
  <el-drawer v-model="createOpen" title="从原文建立关系候选" size="min(780px, 100vw)" @closed="sourceController?.abort()">
    <p class="method-caption">选择当前已发布且审核有效的资料切片，摘录原文中的两个实体。候选保存后仍需人工审核。</p>
    <ErrorPanel :message="createError" /><div v-loading="sourceLoading" class="graph-create">
      <label>选择资料切片<el-select v-model="selectedId" placeholder="请选择原文" class="full-width" popper-class="graph-source-options" :disabled="busy || !!pending" aria-label="图谱资料切片" @change="chooseSource"><el-option v-for="item in sources" :key="item.chunk_id" :value="item.chunk_id" :label="`${item.title} · 版本 ${item.document_version} · ${item.section}`" /></el-select></label>
      <el-pagination v-if="sourceTotal > 10" v-model:current-page="sourcePage" :page-size="10" :total="sourceTotal" layout="prev, pager, next" :disabled="busy || !!pending" />
      <el-empty v-if="!sourceLoading && !sourceTotal && !createError" description="暂无当前有效的已发布资料，请先在知识管理中审核并发布" />
      <template v-if="selected"><label>原文（可选中文字）<textarea ref="original" :value="selected.text" readonly class="graph-textarea" aria-label="图谱切片原文" /></label><el-button :disabled="busy || !!pending" @click="useSelection">使用选中的原文</el-button>
        <p class="method-caption">当前引用 {{ [...quote].length }} 个字符；引用必须为连续的 8 至 1200 个字符，实体名称必须出现在其中。</p><blockquote>{{ quote }}</blockquote>
        <div class="graph-form-grid"><label>起点实体<el-input v-model="draft.subject" maxlength="120" :disabled="busy || !!pending" aria-label="图谱起点实体" /></label><label>起点类型<el-select v-model="draft.subject_kind" :disabled="busy || !!pending" aria-label="图谱起点类型"><el-option v-for="kind in kinds" :key="kind" :value="kind" :label="kind" /></el-select></label>
          <label>终点实体<el-input v-model="draft.object" maxlength="120" :disabled="busy || !!pending" aria-label="图谱终点实体" /></label><label>终点类型<el-select v-model="draft.object_kind" :disabled="busy || !!pending" aria-label="图谱终点类型"><el-option v-for="kind in kinds" :key="kind" :value="kind" :label="kind" /></el-select></label></div>
        <label>关系（从起点指向终点）<el-input v-model="draft.predicate" maxlength="80" :disabled="busy || !!pending" placeholder="例如：计量单位为、采用规则" aria-label="图谱关系说明" /></label>
        <label>摘录说明<el-input v-model="draft.note" type="textarea" :rows="2" maxlength="1000" :disabled="busy || !!pending" aria-label="图谱摘录说明" /></label>
        <el-button type="primary" :loading="busy" :disabled="!!pending || !draft.subject.trim() || !draft.object.trim() || !draft.predicate.trim() || !draft.note.trim()" @click="submitCreate">保存待审核候选</el-button>
      </template>
    </div>
  </el-drawer>
</template>

<style scoped>
.graph-heading{flex-wrap:wrap}.graph-buttons{display:flex;gap:10px;flex-wrap:wrap}.graph-buttons .el-button+.el-button{margin-left:0}.graph-panel,.graph-card{margin-bottom:18px}.graph-card-top{display:flex;justify-content:space-between;gap:14px;align-items:flex-start}.graph-card h2,.graph-detail h2{overflow-wrap:anywhere}.relation-predicate{font-weight:400;color:#648070}.graph-card p{margin:10px 0}.graph-card blockquote,.graph-create blockquote{margin:16px 0;padding:12px 15px;border-left:3px solid #9bc3a9;background:#f5f8f5;white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;line-height:1.9}.graph-warning{color:#906020;font-size:13px;line-height:1.8}.graph-notice{padding:12px 0;color:#27684d;font-size:13px}.graph-detail{line-height:1.9}.graph-detail>p{margin:12px 0}.graph-original{margin:15px 0;padding:15px;background:#f5f8f5;white-space:pre-wrap;overflow-wrap:anywhere}.graph-original mark{background:#e1efcf;color:inherit}.graph-review{padding:0 0 25px}.graph-review label,.graph-create label{display:flex;flex-direction:column;gap:8px;font-size:13px;line-height:1.7}.graph-review .el-checkbox{display:flex;white-space:normal;height:auto;margin:14px 0}.graph-review :deep(.el-checkbox__label){white-space:normal}.graph-create{display:flex;flex-direction:column;gap:18px;margin-top:20px}.graph-textarea{width:100%;height:220px;resize:vertical;border:1px solid #dce5df;border-radius:7px;padding:12px;font:inherit;font-size:13px;line-height:1.9;color:#365548}.graph-form-grid{display:grid;grid-template-columns:2fr 1fr;gap:18px}.graph-form-grid .el-select{width:100%}.graph-action{padding:14px 0;border-bottom:1px solid #e1e9e3}.graph-list{min-height:180px}.graph-detail .mono{padding:15px 0}.graph-create .el-pagination{max-width:100%}
@media(max-width:760px){.graph-heading .graph-buttons{width:100%}.graph-heading .el-button{flex:1}.graph-card-top{flex-direction:column}.graph-form-grid{grid-template-columns:1fr}.graph-card{padding:18px}.graph-review .graph-buttons .el-button{flex:1}}
:global(.graph-source-options){max-width:calc(100vw - 24px)}:global(.graph-source-options .el-select-dropdown__item){height:auto;min-height:34px;white-space:normal;overflow-wrap:anywhere;line-height:1.7;padding-top:7px;padding-bottom:7px}
</style>
