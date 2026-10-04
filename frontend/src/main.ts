import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { ElButton, ElTag, ElTable, ElTableColumn, ElSelect, ElOption, ElInputNumber, ElSlider,
  ElRadioGroup, ElRadioButton, ElPagination, ElCollapse, ElCollapseItem, ElConfigProvider, ElLoading } from 'element-plus'
import 'element-plus/dist/index.css'
import './style.css'
import App from './App.vue'
import { router } from './router'
import { onUnauthorized } from './api'
import { useAuthStore } from './stores/auth'

const app = createApp(App)
app.use(createPinia())
onUnauthorized(() => {
  useAuthStore().clear('会话已到期或撤销，请重新登录。')
  if (router.currentRoute.value.name !== 'login') void router.replace({ name: 'login', query: { next: router.currentRoute.value.fullPath } })
})
// 只注册当前页面使用的组件，避免把尚未使用的全站组件打入入口包。
for (const component of [ElButton, ElTag, ElTable, ElTableColumn, ElSelect, ElOption, ElInputNumber,
  ElSlider, ElPagination, ElCollapse, ElCollapseItem, ElConfigProvider, ElLoading]) app.use(component)
// Radio 子组件的 install 为空，按需使用时需要显式注册。
app.component('ElRadioGroup', ElRadioGroup)
app.component('ElRadioButton', ElRadioButton)
app.use(router)
void router.isReady().then(() => app.mount('#app'))
