import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from './stores/auth'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('./views/LoginView.vue'), meta: { title: '进入工作台' } },
    { path: '/', name: 'overview', component: () => import('./views/OverviewView.vue'), meta: { title: '运行总览' } },
    { path: '/devices', name: 'devices', component: () => import('./views/DevicesView.vue'), meta: { title: '设备工作台' } },
    { path: '/model', name: 'model', component: () => import('./views/ModelView.vue'), meta: { title: '模型与验证' } },
    { path: '/skills', name: 'skills', component: () => import('./views/SkillsView.vue'), meta: { title: '分析能力与版本' } },
    { path: '/analysis', redirect: to => ({ path: '/assistant', query: { ...to.query, mode: 'analysis' } }) },
    { path: '/account', name: 'account', component: () => import('./views/AccountView.vue'), meta: { title: '当前账号' } },
    { path: '/jobs', name: 'jobs', component: () => import('./views/JobsView.vue'), meta: { title: '后台任务' } },
    { path: '/reports', name: 'reports', component: () => import('./views/ReportsView.vue'), meta: { title: '报告中心' } },
    { path: '/knowledge', name: 'knowledge', component: () => import('./views/DocumentsView.vue'), meta: { title: '知识与能力管理' } },
    { path: '/graph', name: 'graph', component: () => import('./views/GraphView.vue'), meta: { title: '知识图谱' } },
    { path: '/assistant', name: 'assistant', component: () => import('./views/AssistantView.vue'), meta: { title: '智能助手' } },
    { path: '/assistant/history', redirect: to => ({ path: '/assistant', query: typeof to.query.answer_id === 'string' ? { legacy_answer_id: to.query.answer_id } : {} }) },
    { path: '/assistant/conversations', redirect: to => ({ path: '/assistant', query: to.query }) },
    { path: '/:pathMatch(.*)*', name: 'not-found', component: () => import('./views/NotFoundView.vue'), meta: { title: '页面不存在' } },
  ],
  scrollBehavior: () => ({ top: 0 }),
})
router.beforeEach(async to => {
  const auth = useAuthStore()
  await auth.restore()
  if (to.name !== 'login' && !auth.authenticated) return { name: 'login', query: { next: to.fullPath } }
  if (to.name === 'login' && auth.authenticated) return { name: 'overview' }
  if (to.path === '/reports' && Object.keys(to.query).length === 0 && auth.user?.permissions.includes('reports:review')) return { path: '/reports', query: { mode: 'review' } }
  // 空入口恢复工作方式；带历史记录的链接始终以原记录为准。
  if (to.path === '/assistant' && Object.keys(to.query).length === 0) {
    const permissions = auth.user?.permissions ?? []
    const last = sessionStorage.getItem(`machineguard.assistant.${auth.user?.id}.mode`)
    if (permissions.includes('analysis:create') && (!permissions.includes('knowledge:manage') || last === 'analysis')) return { path: '/assistant', query: { mode: 'analysis' }, hash: to.hash }
    return { path: '/assistant', query: { mode: 'knowledge' }, hash: to.hash }
  }
  // 旧单轮结果链接继续打开原记录，不把它转换为新会话。
  if (to.path === '/assistant' && typeof to.query.answer_id === 'string') return { path: '/assistant', query: { ...to.query, answer_id: undefined, legacy_answer_id: to.query.answer_id } }
})
router.afterEach(to => { document.title = `${String(to.meta.title)} · MachineGuard` })
