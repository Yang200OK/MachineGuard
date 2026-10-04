<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElDrawer } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import KnowledgeChatView from './KnowledgeChatView.vue'
import AnalysisView from './AnalysisView.vue'
import SkillsView from './SkillsView.vue'

const auth = useAuthStore(); const route = useRoute(); const router = useRouter()
const locked = ref(false); const skillsOpen = ref(false)
const knowledgeAllowed = computed(() => auth.user?.permissions.includes('knowledge:manage'))
const analysisAllowed = computed(() => auth.user?.permissions.includes('analysis:create'))
const mode = computed(() => route.query.mode === 'analysis' ? 'analysis' : 'knowledge')
const prefix = `machineguard.assistant.${auth.user?.id}`
// 每种工作方式保留原记录地址；切换只恢复视图，不提交问题。
watch(() => route.fullPath, value => {
  const target = router.resolve(value)
  const location = router.resolve({ path: target.path, query: { ...target.query, mode: mode.value }, hash: target.hash }).fullPath
  sessionStorage.setItem(`${prefix}.${mode.value}.location`, location)
  sessionStorage.setItem(`${prefix}.mode`, mode.value)
}, { immediate: true })
async function switchMode(value: 'knowledge' | 'analysis') {
  if (locked.value || value === mode.value) return
  const saved = sessionStorage.getItem(`${prefix}.${value}.location`)
  const target = saved ? router.resolve(saved) : null
  if (target?.path === '/assistant' && (target.query.mode === 'analysis' ? 'analysis' : 'knowledge') === value) {
    // 旧缓存可能只有空入口；显式指定目标方式，避免被最近方式重新导向。
    await router.push({ path: target.path, query: { ...target.query, mode: value }, hash: target.hash })
  }
  else await router.push({ path: '/assistant', query: { mode: value } })
}
</script>
<template>
  <section class="assistant-workspace" aria-label="智能助手">
    <div class="assistant-toolbar">
      <strong>智能助手</strong>
      <div class="assistant-modes" role="group" aria-label="助手工作方式">
        <el-button v-if="knowledgeAllowed" :type="mode === 'knowledge' ? 'primary' : 'default'" :aria-pressed="mode === 'knowledge'" :disabled="locked" @click="switchMode('knowledge')">资料问答</el-button>
        <el-button v-if="analysisAllowed" :type="mode === 'analysis' ? 'primary' : 'default'" :aria-pressed="mode === 'analysis'" :disabled="locked" @click="switchMode('analysis')">分析与查询</el-button>
      </div>
      <el-button v-if="auth.user?.permissions.includes('devices:read')" class="capabilities-button" text @click="skillsOpen = true">可用能力</el-button>
    </div>
    <AnalysisView v-if="mode === 'analysis'" @navigation-lock="locked = $event" />
    <KnowledgeChatView v-else @navigation-lock="locked = $event" />
    <el-drawer v-model="skillsOpen" title="可用分析能力" size="min(720px, 100vw)" destroy-on-close><SkillsView v-if="skillsOpen" embedded /></el-drawer>
  </section>
</template>
<style scoped>
.assistant-workspace{--assistant-content-width:1200px;display:flex;flex:1;flex-direction:column;min-height:0;min-width:0;overflow:hidden}.assistant-toolbar{display:flex;align-items:center;gap:18px;padding:12px 24px;border-bottom:1px solid #e0e7da;background:#fff;flex:none}.assistant-toolbar strong{font-size:15px;color:#355747}.assistant-modes{display:flex;gap:8px}.assistant-modes .el-button{margin:0}.capabilities-button{margin-left:auto}@media(max-width:760px){.assistant-toolbar{padding:10px 12px;gap:8px;flex-wrap:wrap}.assistant-toolbar strong{font-size:13px}.assistant-modes{gap:4px}.assistant-modes .el-button,.capabilities-button{font-size:12px;padding:8px}}
</style>
