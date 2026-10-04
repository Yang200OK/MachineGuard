/** 侧栏仅加载本人会话；切换账号或查询词时丢弃旧请求结果。 */
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '../api'
import { useAuthStore } from './auth'
import { cancelled, errorText } from '../format'
import type { KnowledgeConversation } from '../types'

export const useConversationsStore = defineStore('conversations', () => {
  const items = ref<KnowledgeConversation[]>([]); const total = ref(0); const page = ref(1); const search = ref('')
  const deleted = ref(false)
  const loading = ref(false); const error = ref(''); const locked = ref(false)
  let controller: AbortController | undefined
  async function load(reset = false) {
    if (reset) page.value = 1
    const auth = useAuthStore(); const owner = auth.user?.id
    controller?.abort(); controller = new AbortController(); const signal = controller.signal
    items.value = []; total.value = 0; error.value = ''; loading.value = true
    if (!auth.user?.permissions.includes('knowledge:manage')) { loading.value = false; return }
    try {
      const value = await api.conversations(auth.token, (page.value - 1) * 10, signal, search.value.trim(), deleted.value)
      if (!signal.aborted && useAuthStore().user?.id === owner) {
        const last = Math.max(1, Math.ceil(value.total / 10))
        if (page.value > last) { page.value = last; await load(); return }
        items.value = value.items; total.value = value.total }
    } catch (failure) { if (!signal.aborted && !cancelled(failure)) error.value = errorText(failure) }
    finally { if (!signal.aborted) loading.value = false }
  }
  function clear() { controller?.abort(); items.value = []; total.value = 0; search.value = ''; page.value = 1; loading.value = false; error.value = ''; locked.value = false; deleted.value = false }
  return { deleted, items, total, page, search, loading, error, locked, load, clear }
})
