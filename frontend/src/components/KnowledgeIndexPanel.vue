<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { ElInput, ElDrawer } from 'element-plus'
import { api } from '../api'
import { useAuthStore } from '../stores/auth'
import { cancelled, errorText, timestamp } from '../format'
import type { DocumentSummary, KnowledgeSnapshot, SearchPreview } from '../types'
import ErrorPanel from './ErrorPanel.vue'

const emit = defineEmits<{ manage: [documentId: string, versionId: string] }>()
const auth = useAuthStore(); const snapshots = ref<KnowledgeSnapshot[]>([]); const total = ref(0); const page = ref(1)
const candidates = ref<DocumentSummary[]>([]); const candidateTotal = ref(0); const candidatePage = ref(1)
const currentSnapshot = ref<KnowledgeSnapshot | null>(null); const historyOpen = ref(false); const previewOpen = ref(false)
const historyItem = ref<KnowledgeSnapshot | null>(null)
const loading = ref(false); const searching = ref(false); const error = ref(''); const searchError = ref(''); const query = ref(''); const equipment = ref('')
const selectedId = ref('current'); const result = ref<SearchPreview | null>(null)
const labels = { waiting_review: '待人工审核', queued: '等待执行', running: '发布中', succeeded: '发布成功', failed: '发布失败', cancelled: '已取消' }
let controller: AbortController | undefined; let disposed = false; let timer: number | undefined
async function load() {
  window.clearTimeout(timer); controller?.abort(); controller = new AbortController(); const signal = controller.signal
  loading.value = true; error.value = ''
  try {
    const [data, ready] = await Promise.all([api.knowledgeSnapshots(auth.token, (page.value - 1) * 10, signal), api.catalog(auth.token, (candidatePage.value - 1) * 10, '', '', '待发布', false, signal)])
    if (signal.aborted || disposed) return
    snapshots.value = data.items; total.value = data.total; currentSnapshot.value = data.current
    candidates.value = ready.items; candidateTotal.value = ready.total
    if (historyItem.value) historyItem.value = data.items.find(item => item.snapshot_id === historyItem.value!.snapshot_id) ?? null
    // 仅轮询任务状态；刷新不会重新提交索引或调用模型。
    if (data.items.some(item => ['queued', 'running'].includes(item.status))) timer = window.setTimeout(() => void load(), 2000)
  } catch (failure) { if (!cancelled(failure)) error.value = errorText(failure) }
  finally { if (!signal.aborted) loading.value = false }
}
async function search() {
  if (searching.value) return
  searching.value = true; searchError.value = ''; result.value = null
  try {
    const data = await api.searchPreview(auth.token, query.value.trim(), selectedId.value === 'current' ? null : selectedId.value, equipment.value.trim() || null)
    if (!disposed) result.value = data
  } catch (failure) { if (!disposed) searchError.value = errorText(failure) }
  finally { searching.value = false }
}
function clearPreview() { result.value = null; searchError.value = '' }
onMounted(load); onBeforeUnmount(() => { disposed = true; window.clearTimeout(timer); controller?.abort() })
</script>
<template>
  <section class="panel index-panel">
    <div class="panel-heading"><h2>问答正在使用的资料</h2><el-button :loading="loading" @click="load">刷新发布状态</el-button></div>
    <ErrorPanel :message="error" />
    <template v-if="currentSnapshot"><p><strong>{{ currentSnapshot.members.filter(member => member.available !== false).length }} 份有效资料</strong> · 最近发布 {{ timestamp(currentSnapshot.published_at!) }}</p>
      <p class="method-caption">下列版本用于新问答。归档、停用或审核失效的资料会立即退出有效范围。</p>
      <el-table :data="currentSnapshot.members.filter(member => member.available !== false)"><el-table-column label="资料" min-width="220"><template #default="scope">{{ scope.row.source_metadata.title }}</template></el-table-column><el-table-column label="版本" width="80"><template #default="scope">{{ scope.row.version_number }}</template></el-table-column><el-table-column label="问答状态" width="140"><template #default="scope">{{ scope.row.available === false ? '已退出有效范围' : '可用于问答' }}</template></el-table-column><el-table-column label="操作" width="110"><template #default="scope"><el-button text @click="emit('manage', scope.row.document_id, scope.row.version_id)">查看资料</el-button></template></el-table-column></el-table>
      <details v-if="currentSnapshot.members.some(member => member.available === false)" class="snapshot-item"><summary>退出范围的旧资料（{{ currentSnapshot.members.filter(member => member.available === false).length }} 份）</summary><p v-for="member in currentSnapshot.members.filter(member => member.available === false)" :key="member.version_id">{{ member.source_metadata.title }} · 版本 {{ member.version_number }} · 归档、停用或审核失效，不用于新问答</p></details>
      <details class="snapshot-item"><summary>当前发布的技术记录</summary><pre>{{ JSON.stringify({ model: currentSnapshot.model, dimension: currentSnapshot.dimension, snapshot_id: currentSnapshot.snapshot_id, config_sha256: currentSnapshot.config_sha256, manifest_sha256: currentSnapshot.manifest_sha256, usage: currentSnapshot.usage }, null, 2) }}</pre></details>
    </template><p v-else-if="!loading">尚无已发布资料。请先上传、解析并通过审核。</p>
    <div class="snapshot-item"><h3>待发布版本 · {{ candidateTotal }} 份</h3><p class="method-caption">审核通过后在此发布。归档资料须先恢复；恢复不会自动创建发布任务。</p>
      <p v-if="!loading && !candidateTotal">没有待发布版本。新资料或新版本请在资料库中完成解析与审核。</p>
      <el-table v-if="candidates.length" :data="candidates"><el-table-column prop="title" label="资料" min-width="220" /><el-table-column label="版本" width="80"><template #default="scope">{{ scope.row.latest_version.number }}</template></el-table-column><el-table-column label="操作" width="140"><template #default="scope"><el-button type="primary" text @click="emit('manage', scope.row.document_id, scope.row.latest_version.version_id)">核对并发布</el-button></template></el-table-column></el-table>
      <el-pagination v-if="candidateTotal > 10" v-model:current-page="candidatePage" :page-size="10" :total="candidateTotal" layout="prev, pager, next" @current-change="load" />
    </div>
    <div class="panel-actions"><el-button @click="historyItem = null; historyOpen = true">查看发布记录</el-button><el-button @click="previewOpen = true">检索预览</el-button></div>
  </section>
  <el-drawer v-model="historyOpen" title="发布记录" size="min(760px, 100vw)" :close-on-click-modal="false" @closed="historyItem = null">
    <div class="panel-heading"><p>历史失败不影响当前已发布资料。</p><el-button @click="historyOpen = false">关闭记录</el-button></div><ErrorPanel :message="error" />
    <el-table :data="snapshots"><el-table-column label="提交时间" min-width="175"><template #default="scope">{{ timestamp(scope.row.created_at) }}</template></el-table-column><el-table-column label="结果" width="120"><template #default="scope">{{ labels[scope.row.status as keyof typeof labels] }}{{ scope.row.is_current ? ' · 当前' : '' }}</template></el-table-column><el-table-column label="资料数" width="85"><template #default="scope">{{ scope.row.members.length }}</template></el-table-column><el-table-column label="操作" width="80"><template #default="scope"><el-button text @click="historyItem = scope.row">详情</el-button></template></el-table-column></el-table>
    <el-pagination v-if="total > 10" v-model:current-page="page" :page-size="10" :total="total" layout="prev, pager, next" :disabled="loading" @current-change="load" />
    <article v-if="historyItem" class="snapshot-item"><div class="panel-heading"><h3>{{ labels[historyItem.status] }} · {{ historyItem.progress }}%</h3><el-button @click="historyItem = null">收起详情</el-button></div><ErrorPanel :message="historyItem.error ? `${historyItem.error.message}（${historyItem.error.code}）` : ''" />
      <p v-for="member in historyItem.members" :key="member.version_id">{{ member.source_metadata.title }} · 版本 {{ member.version_number }}</p>
      <router-link v-if="historyItem.owner_id === auth.user?.id && !historyItem.job_deleted" :to="{ path: '/jobs', query: { job_id: historyItem.job_id } }">查看后台任务</router-link><p v-else>{{ historyItem.job_deleted ? '任务记录已移入回收站，发布结果仍保留；恢复任务记录后可查看执行详情。' : '此任务由其他管理员提交，可在此查看发布状态。' }}</p>
      <details><summary>模型、用量与发布依据</summary><pre>{{ JSON.stringify(historyItem, null, 2) }}</pre></details>
    </article>
  </el-drawer>
  <el-drawer v-model="previewOpen" title="检索预览" size="min(760px, 100vw)" :close-on-click-modal="false">
    <div class="panel-heading"><p>查询会发送到百炼生成向量并产生模型用量。</p><el-button @click="previewOpen = false">关闭预览</el-button></div><ErrorPanel :message="searchError" />
    <label>查询<el-input v-model="query" aria-label="知识检索查询" maxlength="1000" :disabled="searching" @keyup.enter="query.trim() && search()" /></label>
    <div class="search-fields"><label>资料版本<el-select v-model="selectedId" :disabled="searching"><el-option label="当前已发布资料" value="current" /><el-option v-for="item in snapshots.filter(item => item.published_at)" :key="item.snapshot_id" :label="`发布于 ${timestamp(item.published_at!)}`" :value="item.snapshot_id" /></el-select></label><label>设备类型（可留空）<el-input v-model="equipment" aria-label="检索设备类型" maxlength="200" :disabled="searching" /></label></div>
    <div class="panel-actions"><el-button type="primary" :disabled="!query.trim() || searching" :loading="searching" @click="search">检索已审核知识</el-button><el-button :disabled="searching || !result" @click="clearPreview">清除结果</el-button></div>
    <div v-if="result"><p>{{ result.limitation || result.reason }}</p><p v-if="!result.items.length">当前范围没有有效检索结果。</p>
      <article v-for="item in result.items" :key="item.chunk_id" class="snapshot-item"><strong>{{ item.source_metadata.title }} · 版本 {{ item.document_version }} · 页 {{ item.page }}</strong><p>{{ item.section }}</p><pre class="preview-text">{{ item.text }}</pre><details><summary>检索排名与出处详情</summary><p>向量排名 {{ item.ranks.vector ?? '未入选' }} · 关键词排名 {{ item.ranks.bm25 ?? '未入选' }} · RRF {{ item.rrf_score.toFixed(6) }}</p><pre>{{ JSON.stringify({ source: item.source_metadata, snapshot_id: result.snapshot_id, version_id: item.version_id, parse_id: item.parse_id, chunk_id: item.chunk_id, page_spans: item.page_spans }, null, 2) }}</pre></details></article>
    </div>
  </el-drawer>
</template>
<style scoped>
.index-panel p{line-height:1.8;overflow-wrap:anywhere}.index-panel pre,.snapshot-item pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:300px;overflow:auto;padding:12px;background:var(--el-fill-color-light);font-size:12px;line-height:1.8}.snapshot-item{padding:16px 0;border-top:1px solid var(--el-border-color)}.preview-text{max-height:180px!important}.search-fields{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:12px 0}.search-fields .el-select{width:100%}.panel-actions{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}label{display:block;line-height:2}.panel-heading{flex-wrap:wrap}summary{cursor:pointer}@media(max-width:760px){.search-fields{grid-template-columns:1fr}}
</style>
