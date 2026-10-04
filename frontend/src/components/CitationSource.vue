<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../api'
import { useAuthStore } from '../stores/auth'
import { cancelled, errorText } from '../format'
import type { CitationReference, CitationSource } from '../types'
import ErrorPanel from './ErrorPanel.vue'
const props = defineProps<{ reference: CitationReference; managementLink?: boolean; conversationId?: string; agentRunId?: string; reviewRunId?: string; reportId?: string; pathReference?: { path: number; edge: number } }>()
const auth = useAuthStore(); const data = ref<CitationSource | null>(null); const loading = ref(false); const error = ref('')
const labels: Record<string, string> = { approved: '审核通过', rejected: '审核不通过', pending: '尚未审核', stale: '来源修订后原审核已失效', inactive: '版本已停用', not_ready: '解析尚不可审核' }
let controller: AbortController | undefined
async function load() {
  controller?.abort(); controller = new AbortController(); const signal = controller.signal
  data.value = null; error.value = ''; loading.value = true
  try { const value = props.pathReference && props.agentRunId ? await api.analysisPathSource(auth.token, props.agentRunId, props.pathReference.path, props.pathReference.edge, signal) : props.reportId ? await api.reportCitation(auth.token, props.reportId, props.reference.claim_index, props.reference.citation_index, signal) : props.reviewRunId ? await api.reviewCitation(auth.token, props.reviewRunId, props.reference.claim_index, props.reference.citation_index, signal) : props.agentRunId ? await api.analysisCitation(auth.token, props.agentRunId, props.reference.claim_index, props.reference.citation_index, signal) : await api.citationSource(auth.token, props.reference, signal); if (!signal.aborted) data.value = value }
  catch (failure) { if (!signal.aborted && !cancelled(failure)) error.value = errorText(failure) }
  finally { if (!signal.aborted) loading.value = false }
}
watch(() => props.reference, load, { immediate: true, deep: true })
onBeforeUnmount(() => controller?.abort())
</script>
<template>
  <div class="citation-source" aria-label="引用原文">
    <p v-if="loading" role="status">正在核对引用位置</p><ErrorPanel :message="error" /><el-button v-if="error" @click="load">重新读取原文</el-button>
    <template v-if="data">
      <p v-if="data.saved_revision" class="source-state" :class="{ withdrawn: !data.relation_usable }">本次路径使用审核修订 {{ data.saved_revision }}。当前关系{{ data.relation_usable ? '仍有效' : '已撤回或来源失效' }}，此处保留当时原文。</p>
      <h2>{{ data.citation.source_metadata.title }}</h2><p class="source-meta">回答使用版本 {{ data.citation.document_version }} · {{ data.citation.section || '未标注章节' }}</p>
      <p class="source-state" :class="{ withdrawn: !data.is_active || data.review_status !== 'approved' }">当前状态：{{ data.is_archived ? "资料已归档" : labels[data.review_status] || data.review_status }}。下方为回答生成时对应版本的解析原文。</p>
      <article v-for="(context, index) in data.contexts" :key="index"><h3>{{ data.file_format === 'pdf' ? '实际页' : '逻辑页' }} {{ context.page }} · 引文字符 {{ context.char_start }}–{{ context.char_end }}</h3><p class="source-context"><mark>{{ context.quote }}</mark></p><details class="source-neighborhood"><summary>展开前后文</summary><p class="source-context"><span>{{ context.before }}</span><mark>{{ context.quote }}</mark><span>{{ context.after }}</span></p></details></article>
      <details class="source-information"><summary>来源与使用范围</summary><dl><dt>作者或机构</dt><dd>{{ data.citation.source_metadata.author_or_organization }}</dd><dt>资料类型</dt><dd>{{ ({ original: '原始来源', project_summary: '项目概括', simulated_sop: '模拟 SOP' } as Record<string, string>)[data.citation.source_metadata.source_kind] }}</dd><dt>适用范围</dt><dd>{{ data.citation.source_metadata.applicable_scope }}</dd><dt>使用边界</dt><dd>{{ data.citation.source_metadata.usage_boundary }}</dd><dt>许可</dt><dd>{{ data.citation.source_metadata.license }}</dd></dl></details>
      <router-link v-if="managementLink" class="text-link" :to="{ path: '/knowledge', query: { document_id: data.citation.source_id, version_id: data.citation.version_id, answer_id: reference.answer_id, claim_index: String(reference.claim_index), citation_index: String(reference.citation_index), conversation_id: conversationId } }">在知识管理查看此版本</router-link>
      <details class="source-information"><summary>技术定位记录</summary><pre>{{ JSON.stringify({ reference, version_id: data.citation.version_id, parse_id: data.citation.parse_id, chunk_id: data.citation.chunk_id, spans: data.citation.page_spans }, null, 2) }}</pre></details>
    </template>
  </div>
</template>
<style scoped>
.citation-source{overflow-wrap:anywhere;color:#36584a;font-size:13px}.citation-source h2{font-size:19px;line-height:1.6}.source-meta{color:#789183;margin:12px 0}.source-state{padding:12px;background:#eef5ef;border-radius:8px;line-height:1.8}.source-state.withdrawn{background:#faf1dd;color:#80652b}.citation-source article{margin:24px 0}.citation-source h3{font-size:12px;color:#728979;margin:12px 0}.source-context{white-space:pre-wrap;font-size:14px;line-height:2;background:#f6f8f5;padding:18px;border-radius:8px}.source-context span{color:#7d8e82}.source-neighborhood{margin-top:12px;color:#728979;font-size:12px}.source-neighborhood summary{cursor:pointer;margin-bottom:10px}.source-context mark{color:#244c38;background:#dceec5;padding:2px 0}.source-information{border-top:1px solid #e1e9df;padding:16px 0}.source-information summary{cursor:pointer}.source-information dl{margin:15px 0;line-height:1.8}.source-information dt{color:#8a9a8c}.source-information dd{margin:3px 0 12px}.source-information pre{overflow:auto;white-space:pre-wrap;font-size:11px;max-height:260px}
</style>
