<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../api'
import { useAuthStore } from '../stores/auth'
import { cancelled, errorText } from '../format'
import type { DocumentVersion, ParsedPage } from '../types'
import ErrorPanel from './ErrorPanel.vue'
const props = defineProps<{ version: DocumentVersion; parseId?: string }>()
const auth = useAuthStore(); const pages = ref<ParsedPage[]>([]); const offset = ref(0); const total = ref(0); const error = ref(''); const loading = ref(false)
let controller: AbortController | undefined
const sections = computed(() => pages.value.flatMap(page => page.text.split(/(?=^#{1,3} )/m).filter(text => text.trim()).map((text, i) => ({ id: `page-${page.page}-${i}`, title: text.split('\n')[0]!.replace(/^#+\s*/, ''), heading: /^#{1,3} /.test(text), body: /^#{1,3} /.test(text) ? text.split('\n').slice(1).join('\n').trim() : text, page: page.page }))))
async function load() {
  controller?.abort(); controller = new AbortController(); const signal = controller.signal
  error.value = ''; pages.value = []; loading.value = true
  try {
    const id = props.parseId ?? props.version.latest_parse?.parse_id
    if (!id || (!props.parseId && props.version.latest_parse?.status !== 'succeeded')) throw new Error('本版本尚无可阅读的解析正文，请在“发布与管理”中核对。')
    const data = await api.parsePages(auth.token, id, offset.value, signal)
    if (!signal.aborted) { pages.value = data.items; total.value = data.total }
  } catch (failure) { if (!signal.aborted && !cancelled(failure)) error.value = errorText(failure) }
  finally { if (!signal.aborted) loading.value = false }
}
function jump(id: string) { document.getElementById(id)?.scrollIntoView({ block: 'start', behavior: 'smooth' }) }
watch(() => [props.version.version_id, props.parseId], () => { offset.value = 0; void load() }, { immediate: true })
onBeforeUnmount(() => controller?.abort())
</script>
<template>
  <div class="document-reader"><p class="method-caption">版本 {{ version.number }} · {{ version.file_format === 'pdf' ? 'PDF 实际页' : '解析逻辑页（不等于 PDF 页码）' }}</p><ErrorPanel :message="error" retry @retry="load" /><p v-if="loading" role="status">正在读取正文</p>
    <details v-if="sections.length" class="reader-toc" open><summary>本页目录</summary><button v-for="section in sections" :key="section.id" @click="jump(section.id)">{{ section.title }}</button></details>
    <article v-for="section in sections" :id="section.id" :key="section.id"><small>{{ version.file_format === 'pdf' ? '实际页' : '逻辑页' }} {{ section.page }}</small><h3 v-if="section.heading">{{ section.title }}</h3><pre>{{ section.body }}</pre></article>
    <div v-if="total > 4" class="reader-pages"><el-button :disabled="offset === 0 || loading" @click="offset -= 4; load()">前四页</el-button><span>{{ offset + 1 }}–{{ Math.min(offset + 4, total) }} / {{ total }}</span><el-button :disabled="offset + 4 >= total || loading" @click="offset += 4; load()">后四页</el-button></div>
  </div>
</template>
<style scoped>
.reader-toc{background:#f4f7f1;border-radius:8px;padding:14px;margin:18px 0}.reader-toc summary{cursor:pointer}.reader-toc button{display:block;border:0;background:transparent;color:#55744d;text-align:left;cursor:pointer;padding:7px 0;max-width:100%;overflow-wrap:anywhere;font:inherit}.document-reader article{scroll-margin-top:20px;border-top:1px solid #e5ebe1;padding:18px 0}.document-reader pre{font:inherit;line-height:2;white-space:pre-wrap;overflow-wrap:anywhere;max-height:none;color:#355747}.document-reader h3{font-size:16px;line-height:1.8;margin:10px 0}.document-reader small{color:#85967a}.reader-pages{display:flex;justify-content:space-between;align-items:center;gap:10px}
</style>
