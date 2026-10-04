<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from './stores/auth'
import { useSelectionStore } from './stores/selection'
import { errorText, roleLabel } from './format'
import AppIcon from './components/AppIcon.vue'
import ErrorPanel from './components/ErrorPanel.vue'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import { useConversationsStore } from './stores/conversations'

const route = useRoute(); const router = useRouter(); const auth = useAuthStore()
const signingOut = ref(false); const error = ref('')
const history = useConversationsStore()
const collapsed = ref(localStorage.getItem('machineguard.sidebar.collapsed') === 'true'); const mobileOpen = ref(false)
const sidebarElement = ref<HTMLElement>(); const mobileButton = ref<HTMLButtonElement>()
const media = window.matchMedia('(max-width:760px)'); const mobile = ref(media.matches)
const updateMobile = () => { mobile.value = media.matches; if (!mobile.value) mobileOpen.value = false }
media.addEventListener('change', updateMobile)
onBeforeUnmount(() => media.removeEventListener('change', updateMobile))
watch(mobileOpen, async value => { await nextTick(); if (value) sidebarElement.value?.querySelector<HTMLButtonElement>('.mobile-close')?.focus(); else if (mobile.value) mobileButton.value?.focus() })
function sidebarKey(event: KeyboardEvent) {
  if (!mobileOpen.value) return
  if (event.key === 'Escape') { mobileOpen.value = false; return }
  if (event.key !== 'Tab') return
  const nodes = [...(sidebarElement.value?.querySelectorAll<HTMLElement>('a[href],button:not(:disabled),input') ?? [])].filter(el => el.offsetParent !== null)
  const first = nodes[0]; const last = nodes[nodes.length - 1]
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
}
const assistant = computed(() => route.path.startsWith('/assistant'))
const chatting = computed(() => route.name === 'assistant')
const knowledgeChatting = computed(() => chatting.value && route.query.mode !== 'analysis')
// 长追溯详情只在消息区滚动，防止折叠内容把页面撑出视口。
watch(chatting, value => document.documentElement.classList.toggle('knowledge-chat-page', value), { immediate: true })
onBeforeUnmount(() => document.documentElement.classList.remove('knowledge-chat-page'))
function toggleSidebar() { collapsed.value = !collapsed.value; localStorage.setItem('machineguard.sidebar.collapsed', String(collapsed.value)) }
function activeLink(path: string) { return path === '/assistant' ? assistant.value : route.path === path }
watch(() => auth.user?.id, () => { history.clear(); if (knowledgeChatting.value) void history.load(true) })
watch(knowledgeChatting, value => { if (value) void history.load(true) }, { immediate: true })
watch(() => route.fullPath, () => { mobileOpen.value = false })
const visibleLinks = computed(() => [
  { path: '/', title: '运行总览', icon: 'overview' as const },
  { path: '/devices', title: '设备工作台', icon: 'devices' as const },
  ...(auth.user?.permissions.some(permission => ['analysis:create', 'knowledge:manage'].includes(permission)) ? [{ path: '/assistant', title: '智能助手', icon: 'chat' as const }] : []),
  ...(auth.user?.permissions.some(permission => ['analysis:create', 'reports:review'].includes(permission)) ? [{ path: '/reports', title: '报告中心', icon: 'model' as const }] : []),
])
const managementLinks = computed(() => [
  ...(auth.user?.permissions.includes('knowledge:manage') ? [{ path: '/knowledge', title: '知识与能力管理', icon: 'model' as const }] : []),
  ...(auth.user?.permissions.includes('knowledge:manage') ? [{ path: '/graph', title: '知识图谱', icon: 'model' as const }] : []),
  { path: '/model', title: '模型与验证', icon: 'model' as const },
  ...(auth.user?.permissions.includes('system:read') ? [{ path: '/jobs', title: '后台任务', icon: 'model' as const }] : []),
])
const managementOpen = ref(localStorage.getItem('machineguard.management.expanded') === 'true')
// 管理页的直接链接自动展开所在分组，保留旧地址与访问权限。
watch(() => route.path, path => { if (managementLinks.value.some(link => link.path === path)) managementOpen.value = true }, { immediate: true })
function toggleManagement() {
  if (collapsed.value && !mobile.value) { toggleSidebar(); managementOpen.value = true }
  else managementOpen.value = !managementOpen.value
  localStorage.setItem('machineguard.management.expanded', String(managementOpen.value))
}
async function logout() {
  if (signingOut.value) return
  signingOut.value = true; error.value = ''
  try { await auth.logout(); useSelectionStore().clear(); await router.replace('/login') }
  catch (failure) { error.value = errorText(failure) }
  finally { signingOut.value = false }
}
</script>
<template>
  <el-config-provider :locale="zhCn">
  <RouterView v-if="route.name === 'login'" />
  <div v-else class="app-shell" :class="{ 'is-chat': chatting }">
    <aside ref="sidebarElement" class="sidebar" :class="{ 'is-collapsed': collapsed, 'mobile-open': mobileOpen }" :inert="mobile && !mobileOpen" :aria-hidden="mobile && !mobileOpen ? true : undefined" aria-label="工作空间侧栏" @keydown="sidebarKey">
      <button class="sidebar-toggle" :aria-label="collapsed ? '展开侧栏' : '收起侧栏'" :aria-expanded="!collapsed" @click="toggleSidebar"><AppIcon name="panel" /></button>
      <button class="mobile-close" aria-label="关闭侧栏" @click="mobileOpen = false"><AppIcon name="close" /></button>
      <RouterLink to="/" class="brand"><img src="/favicon.svg" alt="" /><span>MachineGuard<small>设备健康工作台</small></span></RouterLink>
      <div class="sidebar-section">工作空间</div>
      <nav aria-label="主导航"><RouterLink v-for="link in visibleLinks" :key="link.path" :to="link.path" :title="link.title" :aria-label="link.title" :class="{ active: activeLink(link.path) }"><AppIcon :name="link.path === '/assistant' ? 'chat' : link.icon" /><span class="nav-title">{{ link.title }}</span><span v-if="activeLink(link.path)" class="nav-mark" /></RouterLink></nav>
      <section class="management-section"><button class="management-toggle" aria-label="管理区" :aria-expanded="managementOpen" aria-controls="management-navigation" @click="toggleManagement"><AppIcon name="panel" /><span class="nav-title">管理区</span><span class="nav-title management-state">{{ managementOpen ? '收起' : '展开' }}</span></button><nav v-if="managementOpen" id="management-navigation" aria-label="管理导航"><RouterLink v-for="link in managementLinks" :key="link.path" :to="link.path" :title="link.title" :aria-label="link.title" :class="{ active: activeLink(link.path) }"><AppIcon :name="link.icon" /><span class="nav-title">{{ link.title }}</span><span v-if="activeLink(link.path)" class="nav-mark" /></RouterLink></nav></section>
      <div class="sidebar-foot"><span class="sidebar-pill">NASA C-MAPSS / FD001</span><p>模拟设备观测<br />可追溯的运行周期预测</p></div>
    </aside>
    <button v-if="mobileOpen" class="sidebar-backdrop" aria-label="关闭侧栏" @click="mobileOpen = false" />
    <div class="workspace" :inert="mobile && mobileOpen" :class="{ 'sidebar-collapsed': collapsed, 'chat-workspace': chatting }">
      <header class="topbar"><button ref="mobileButton" class="mobile-sidebar-button" aria-label="展开侧栏" :aria-expanded="mobileOpen" @click="mobileOpen = true"><AppIcon name="panel" /></button><div class="breadcrumb">工作空间 <span>/</span> <strong>{{ route.meta.title }}</strong></div><RouterLink to="/account" class="user-pill" aria-label="当前账号" title="当前账号与权限"><span class="user-avatar">{{ auth.user?.is_guest ? '访' : auth.user?.display_name.slice(0, 1) }}</span><span>{{ auth.user?.display_name }}<small>{{ auth.user?.roles.map(role => roleLabel[role]).join(' / ') }}</small></span></RouterLink><el-button text :loading="signingOut" @click="logout"><AppIcon name="logout" />退出</el-button></header>
      <main class="main-content"><ErrorPanel :message="error" /><RouterView /></main>
      <footer class="workspace-footer"><span>MachineGuard · NASA 模拟数据</span><span>RUL 单位为运行周期 · 预测不代表已确认故障</span></footer>
    </div>
  </div>
  </el-config-provider>
</template>
<style>
/* 聊天限制外层滚动；其他工作台页面沿用已有布局。 */
html.knowledge-chat-page{height:100%;overflow:hidden}html.knowledge-chat-page body{height:100%;overflow:hidden}.app-shell.is-chat{height:100dvh;min-height:0;overflow:hidden}
.sidebar .sidebar-toggle,.mobile-close,.mobile-sidebar-button{display:flex;align-items:center;justify-content:center;background:transparent;border:0;color:inherit;cursor:pointer;width:32px;height:32px;flex:none}.sidebar-toggle{position:absolute;right:12px;top:14px}.sidebar .sidebar-toggle .app-icon,.mobile-close .app-icon,.mobile-sidebar-button .app-icon{width:21px;height:21px}.sidebar .brand{margin-bottom:30px}.sidebar.with-history{padding-top:52px}.sidebar.with-history .brand{margin-bottom:20px}.sidebar.with-history nav{gap:2px;margin-bottom:18px}.sidebar.with-history nav a{padding:9px 10px;font-size:12px}.sidebar.with-history .sidebar-section,.sidebar.with-history .sidebar-foot{display:none}.sidebar.is-collapsed{width:68px;padding:56px 8px 20px}.sidebar.is-collapsed .brand{justify-content:center;margin-bottom:24px}.sidebar.is-collapsed .brand span,.sidebar.is-collapsed .nav-title,.sidebar.is-collapsed .sidebar-section,.sidebar.is-collapsed .sidebar-foot,.sidebar.is-collapsed .conversation-history{display:none}.sidebar.is-collapsed nav a{justify-content:center;padding:13px 8px}.sidebar.is-collapsed .sidebar-toggle{right:18px}.workspace.sidebar-collapsed{margin-left:68px}.collapsed-new{display:none;justify-content:center;color:#bdd5c6;padding:15px 0;border:0;background:transparent;width:100%;cursor:pointer}.sidebar.is-collapsed .collapsed-new{display:flex}.collapsed-new .app-icon{width:20px;height:20px}.mobile-close,.mobile-sidebar-button,.sidebar-backdrop{display:none}.chat-workspace{height:100dvh;overflow:hidden}.chat-workspace .topbar{height:62px;flex:none}.chat-workspace .main-content{max-width:none;padding:0;min-height:0;display:flex;flex-direction:column;overflow:hidden}.chat-workspace .workspace-footer{display:none}
@media(max-width:760px){.sidebar,.sidebar.is-collapsed{position:fixed;top:0;bottom:0;left:0;width:265px;max-width:88vw;padding:52px 18px 20px;transform:translateX(-100%);gap:0;z-index:42}.sidebar.mobile-open{transform:translateX(0)}.sidebar .sidebar-toggle{display:none}.sidebar .mobile-close{display:flex;position:absolute;right:12px;top:12px;color:#d5e5d9}.mobile-sidebar-button{display:flex;color:#547360}.sidebar-backdrop{display:block;position:fixed;inset:0;background:#132d2a66;border:0;z-index:41}.workspace.sidebar-collapsed{margin-left:0}.sidebar .brand,.sidebar.is-collapsed .brand{justify-content:flex-start;margin-bottom:24px}.sidebar.is-collapsed .brand span,.sidebar.is-collapsed .nav-title,.sidebar.is-collapsed .conversation-history{display:flex}.sidebar.is-collapsed .brand span{display:block}.sidebar nav{flex-direction:column;margin-bottom:20px;gap:4px}.sidebar nav a,.sidebar.is-collapsed nav a{justify-content:flex-start;padding:10px;font-size:12px;gap:12px;flex:none}.sidebar.is-collapsed .collapsed-new{display:none}.sidebar:not(.with-history){overflow:auto}.chat-workspace .topbar{height:58px}}
/* 管理能力保留原权限，收进次级入口，账号从顶部头像进入。 */
.management-section{margin-top:20px;border-top:1px solid #ffffff18;padding-top:14px}.management-toggle{display:flex;align-items:center;gap:12px;width:100%;padding:10px 13px;margin-bottom:6px;border:0;background:transparent;color:#91b6a4;font:inherit;font-size:12px;text-align:left;cursor:pointer}.management-toggle .app-icon{width:20px;height:20px;flex:none}.management-state{margin-left:auto;font-size:10px}.sidebar.is-collapsed .management-toggle{justify-content:center;padding:10px 0}.sidebar{overflow-y:auto}
@media(max-width:760px){.management-section{margin-top:8px}.sidebar.is-collapsed .management-toggle{justify-content:flex-start;padding:10px 13px}.sidebar.is-collapsed .management-toggle .nav-title{display:inline}.management-state{margin-left:auto}}
</style>
