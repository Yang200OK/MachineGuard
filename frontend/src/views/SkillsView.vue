<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { ElDrawer } from 'element-plus'
import { api } from '../api'
import { useAuthStore } from '../stores/auth'
import { cancelled, errorText } from '../format'
import type { SkillSummary, SkillDetail } from '../types'
import ErrorPanel from '../components/ErrorPanel.vue'

defineProps<{ embedded?: boolean }>()
const auth = useAuthStore()
const items = ref<SkillSummary[]>([]); const loading = ref(false); const error = ref('')
const detail = ref<SkillDetail>(); const opened = ref(false); const detailError = ref(''); const detailLoading = ref(false)
let controller: AbortController | undefined; let detailController: AbortController | undefined
const titles: Record<string, string> = { 'engine-analysis': '设备分析', 'sensor-trend-analysis': '传感器趋势', 'maintenance-qa': '资料问答', 'report-generation': '报告生成', 'evidence-path-analysis': '证据路径分析' }
async function load() {
  controller?.abort(); controller = new AbortController(); const signal = controller.signal
  loading.value = true; error.value = ''; items.value = []
  try { const result = await api.skills(auth.token, signal); if (!signal.aborted) items.value = result.items }
  catch (failure) { if (!cancelled(failure)) error.value = errorText(failure) }
  finally { if (!signal.aborted) loading.value = false }
}
async function inspect(item: SkillSummary) {
  detailController?.abort(); detailController = new AbortController(); const signal = detailController.signal
  detail.value = undefined; detailError.value = ''; detailLoading.value = true; opened.value = true
  try { const result = await api.skill(auth.token, item.name, item.version, signal); if (!signal.aborted) detail.value = result }
  catch (failure) { if (!cancelled(failure)) detailError.value = errorText(failure) }
  finally { if (!signal.aborted) detailLoading.value = false }
}
function close() { detailController?.abort(); opened.value = false; detail.value = undefined; detailError.value = ''; detailLoading.value = false }
onMounted(load); onBeforeUnmount(() => { controller?.abort(); detailController?.abort() })
</script>
<template>
  <div v-if="!embedded" class="page-heading"><div><div class="eyebrow">SKILLS</div><h1>分析能力与版本</h1><p>查看设备分析的任务说明、可用工具与版本。</p></div><el-button :loading="loading" @click="load">刷新状态</el-button></div>
  <ErrorPanel :message="error" retry @retry="load" />
  <div v-if="!embedded" class="scope-note"><strong>智能分析已接入模型选择与受控工具</strong><p>设备、周期与预测数值由业务服务校验；资料结论逐条核对原文。每次分析最多六次模型调用、八次工具调用。</p><RouterLink to="/analysis" class="text-link">进入智能分析</RouterLink></div>
  <div v-loading="loading" class="skill-grid">
    <section v-for="item in items" :key="item.name" class="panel skill-panel">
      <div class="panel-heading"><h2>{{ titles[item.name] }}</h2><el-tag :type="item.candidate_available ? 'warning' : item.status === 'available' ? 'success' : 'info'">{{ item.candidate_available ? '报告候选已接入' : item.status === 'available' ? '工具已接入' : '尚未开放' }}</el-tag></div>
      <p>{{ item.description }}</p><p v-if="item.reason" class="method-caption">{{ item.reason }}</p>
      <div v-if="!embedded" class="skill-version">启用版本 {{ item.version }}<span>已登记 {{ item.versions.length }} 个版本</span></div>
      <p v-if="!embedded" class="method-caption">{{ item.tools.length ? `${item.tools.length} 个授权工具` : '没有可执行工具' }}</p>
      <el-button @click="inspect(item)">查看能力详情</el-button>
    </section>
  </div>
  <el-drawer v-model="opened" title="任务说明与工具" size="min(680px, 100vw)" @closed="close">
    <ErrorPanel :message="detailError" /><div v-loading="detailLoading">
      <template v-if="detail"><h2>{{ titles[detail.name] }} · {{ detail.version }}</h2><p v-if="detail.reason">{{ detail.reason }}</p>
        <pre class="skill-body">{{ detail.body }}</pre>
        <h3>代码允许的工具</h3><p v-if="!detail.tool_schemas.length">当前没有可执行工具。</p>
        <div v-for="tool in detail.tool_schemas" :key="tool.function.name" class="skill-tool"><strong>{{ tool.function.name }}</strong><p>{{ tool.function.description }}</p><p v-if="tool.writes_prediction" class="method-caption">会幂等保存本人预测记录。</p></div>
        <el-collapse><el-collapse-item title="查看版本指纹与参数契约" name="contracts"><p class="mono skill-hash">{{ detail.sha256 }}</p><pre class="skill-body">{{ JSON.stringify(detail.tool_schemas, null, 2) }}</pre></el-collapse-item></el-collapse>
      </template>
    </div>
  </el-drawer>
</template>
<style scoped>
.skill-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px;margin-top:20px}.skill-panel{margin:0}.skill-panel p{line-height:1.8}.skill-version{display:flex;gap:14px;flex-wrap:wrap;color:#547360;font-size:13px}.skill-version span{color:#738078}.skill-body{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.8;font-family:inherit;font-size:14px}.skill-tool{padding:14px 0;border-bottom:1px solid #e4ebe6}.skill-tool strong,.skill-hash{overflow-wrap:anywhere}.skill-tool p{line-height:1.7}@media(max-width:760px){.skill-grid{grid-template-columns:1fr}.skill-panel .panel-heading{align-items:flex-start;gap:12px}}
</style>
