<script setup lang="ts">
import { onBeforeUnmount, reactive, ref, watch } from 'vue'
import { ElInput } from 'element-plus'
import { api } from '../api'
import { useAuthStore } from '../stores/auth'
import { cancelled, errorText } from '../format'
import type { GraphEntity, GraphPathQuery, GraphPathResult } from '../graphTypes'
import EvidencePaths from './EvidencePaths.vue'
import ErrorPanel from './ErrorPanel.vue'
defineEmits<{ inspect: [id: string] }>()
const props = defineProps<{ entity?: GraphEntity }>()
const auth = useAuthStore(); const result = ref<GraphPathResult>(); const error = ref(''); const loading = ref(false)
const form = reactive({ subject: '', target: '', document_id: '', max_hops: 2 as 1 | 2 })
let controller: AbortController | undefined
async function search() {
  controller?.abort(); controller = new AbortController(); const signal = controller.signal
  loading.value = true; result.value = undefined; error.value = ''
  const body: GraphPathQuery = { subject: form.subject.trim(), target: form.target.trim() || null, document_id: form.document_id.trim() || null, max_hops: form.max_hops, limit: 5 }
  try { const value = await api.graphPaths(auth.token, body, signal); if (!signal.aborted) result.value = value }
  catch (failure) { if (!signal.aborted && !cancelled(failure)) error.value = errorText(failure) }
  finally { if (!signal.aborted) loading.value = false }
}
function choose(id: string) { form.document_id = id; void search() }
watch(() => props.entity, value => {
  if (!value) return
  form.subject = value.name; form.target = ''; form.document_id = value.document_id; form.max_hops = 2
  void search()
}, { immediate: true })
onBeforeUnmount(() => controller?.abort())
</script>

<template>
  <section class="graph-path-search"><p class="method-caption">沿已审核的箭头查询最多两跳；点击结果中的“查看原文”核对出处。查询覆盖当前有效图谱，结果可能包含其他分页中的关系。</p><p v-if="entity" class="method-caption">已限定资料：{{ entity.document_title }}</p>
    <form class="path-form" @submit.prevent="search"><label>起点实体<el-input v-model="form.subject" maxlength="120" placeholder="例如：剩余运行周期" aria-label="路径起点实体" /></label>
      <label>终点实体（可选）<el-input v-model="form.target" maxlength="120" placeholder="例如：运行周期" aria-label="路径终点实体" /></label>
      <label>最大跳数<el-select v-model="form.max_hops" aria-label="路径最大跳数"><el-option :value="1" label="一跳" /><el-option :value="2" label="两跳" /></el-select></label>
      <details><summary>限定文档（同名实体时需要）</summary><el-input v-model="form.document_id" placeholder="文档 UUID；也可从歧义候选选择" aria-label="路径文档范围" /></details>
      <el-button type="primary" native-type="submit" :loading="loading" :disabled="!form.subject.trim()">查询路径</el-button>
    </form><ErrorPanel :message="error" />
    <EvidencePaths v-if="result" :result="result" choose-document @document="choose" @source="(path, edge) => $emit('inspect', result!.paths[path]!.edges[edge]!.relation_id)" />
  </section>
</template>

<style scoped>
.path-form{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:22px 0}.path-form label{display:flex;flex-direction:column;gap:8px;font-size:13px}.path-form details{align-self:end;font-size:13px;overflow-wrap:anywhere}.path-form summary{cursor:pointer;margin-bottom:8px}.path-form>.el-button{justify-self:start;margin:0}.graph-path-search{margin-top:16px}.path-form .el-select{width:100%}@media(max-width:760px){.path-form{grid-template-columns:1fr}.graph-path-search{padding:18px}}
</style>
