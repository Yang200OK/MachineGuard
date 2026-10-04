<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { errorText } from '../format'
import ErrorPanel from '../components/ErrorPanel.vue'
import AppIcon from '../components/AppIcon.vue'

const auth = useAuthStore(); const router = useRouter(); const route = useRoute()
const username = ref(''); const password = ref(''); const busy = ref(false); const error = ref('')
async function enter(mode: 'login' | 'guest') {
  if (busy.value) return
  busy.value = true; error.value = ''
  try {
    if (mode === 'login') await auth.login(username.value, password.value)
    else await auth.guest()
    password.value = ''
    const next = typeof route.query.next === 'string' && route.query.next.startsWith('/') && !route.query.next.startsWith('//') ? route.query.next : '/'
    await router.replace(next === '/login' ? '/' : next)
  } catch (failure) { error.value = errorText(failure); password.value = '' }
  finally { busy.value = false }
}
async function reconnect() { await auth.restore(true); if (auth.authenticated) await router.replace('/') }
</script>
<template>
  <main class="login-page">
    <section class="login-story">
      <a href="/login" class="brand"><img src="/favicon.svg" alt="" /><span>MachineGuard<small>设备健康工作台</small></span></a>
      <div class="story-content"><div class="eyebrow light">OBSERVE · PREDICT · TRACE</div><h1>读懂设备的变化，<br />让每次预测<br /><em>都有依据。</em></h1><p>从连续观测到剩余运行周期，<br />把设备趋势、可信预测与历史记录放在一起。</p>
        <div class="signal-art" aria-hidden="true"><svg viewBox="0 0 560 190"><defs><pattern id="grid" width="35" height="35" patternUnits="userSpaceOnUse"><path d="M35 0H0V35" fill="none" stroke="#447366" stroke-width=".5" /></pattern></defs><rect width="560" height="190" fill="url(#grid)" /><path d="M0 140L35 143 65 124 95 131 122 106 151 115 175 82 198 99 224 58 249 79 280 45 302 72 331 62 353 95 387 87 412 126 435 109 470 141 500 121 530 142 560 136" fill="none" stroke="#88d5b9" stroke-width="3" /><path d="M0 155C100 163 130 128 210 117S360 110 430 143 520 157 560 151" fill="none" stroke="#5d9f8b" stroke-dasharray="6 6" /></svg><span>连续观测 / 可追溯预测</span></div>
      </div><div class="story-foot">NASA C-MAPSS FD001<span>模拟数据 · 单位为运行周期</span></div>
    </section>
    <section class="login-form-area"><div class="login-form-card"><div class="eyebrow">WELCOME TO MACHINEGUARD</div><h2>进入设备工作台</h2><p class="muted">登录已有账号，或以访客身份查看示例设备。</p>
      <ErrorPanel :message="error || auth.notice" />
      <el-button v-if="auth.token && !auth.authenticated" class="reconnect" :loading="auth.restoring" @click="reconnect">重新连接以验证原会话</el-button>
      <form @submit.prevent="enter('login')">
        <label for="username">账号</label><input id="username" v-model="username" name="username" autocomplete="username" required maxlength="64" placeholder="请输入账号" :disabled="busy" />
        <label for="password">密码</label><input id="password" v-model="password" name="password" type="password" autocomplete="current-password" required maxlength="128" placeholder="请输入密码" :disabled="busy" />
        <el-button native-type="submit" type="primary" class="full-width" size="large" :loading="busy">登录工作台<AppIcon name="arrow" /></el-button>
      </form>
      <div class="login-divider"><span>或体验示例</span></div><el-button class="full-width" size="large" :disabled="busy || auth.restoring" @click="enter('guest')">以访客身份进入</el-button>
      <p class="login-note">访客可查看 6 台示例设备并生成预测。会话有效 30 分钟，历史仅对当前会话身份可见。</p>
    </div><p class="login-bottom">预测支持退化风险观察，具体维护决策需要人工判断。</p></section>
  </main>
</template>
