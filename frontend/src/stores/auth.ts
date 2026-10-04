import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { api, ApiError } from '../api'
import type { AuthSession, User } from '../types'

const SESSION_KEY = 'machineguard.session'
const SELECTION_KEY = 'machineguard.selection'

export const useAuthStore = defineStore('auth', () => {
  // 令牌只保存在当前标签页会话，不保存密码；刷新必须向服务器重新验证。
  const token = ref(sessionStorage.getItem(SESSION_KEY) ?? '')
  const user = ref<User | null>(null)
  const expiresAt = ref('')
  const notice = ref('')
  const restoring = ref(false)
  let checked = false
  let restorePromise: Promise<void> | null = null
  const authenticated = computed(() => !!user.value && !!token.value)

  function clear(message = '') {
    token.value = ''; user.value = null; expiresAt.value = ''; notice.value = message
    sessionStorage.removeItem(SESSION_KEY)
    sessionStorage.removeItem(SELECTION_KEY)
    checked = true
  }
  function accept(session: AuthSession) {
    sessionStorage.removeItem(SELECTION_KEY)
    sessionStorage.setItem(SESSION_KEY, session.access_token)
    token.value = session.access_token; user.value = session.user; expiresAt.value = session.expires_at
    notice.value = ''; checked = true
  }
  async function restore(force = false) {
    if (restorePromise) return restorePromise
    if (checked && !force) return
    if (!token.value) { checked = true; return }
    restoring.value = true; notice.value = ''; user.value = null
    restorePromise = (async () => {
      try {
        const result = await api.me(token.value)
        user.value = result.user; expiresAt.value = result.expires_at
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) clear('会话已到期或撤销，请重新登录。')
        else notice.value = error instanceof Error ? error.message : '身份验证失败。'
      } finally { checked = true; restoring.value = false; restorePromise = null }
    })()
    return restorePromise
  }
  async function login(username: string, password: string) { accept(await api.login(username, password)) }
  async function guest() { accept(await api.guest()) }
  async function logout() { await api.logout(token.value); clear() }
  return { token, user, expiresAt, notice, restoring, authenticated, clear, restore, login, guest, logout }
})
