<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import { useConversationsStore } from '../stores/conversations'
import { ElMessageBox } from 'element-plus'
import { api } from '../api'
import { useAuthStore } from '../stores/auth'
import { errorText } from '../format'
import { timestamp } from '../format'
import ErrorPanel from './ErrorPanel.vue'
import AppIcon from './AppIcon.vue'
const route = useRoute(); const router = useRouter(); const history = useConversationsStore()
const auth = useAuthStore()
async function remove(id: string, title: string) {
  if (history.locked) return
  try {
    if (!history.deleted) await ElMessageBox.confirm(`删除“${title}”？会话将移入回收站，可恢复；正在执行时请先取消。`, '删除会话', { confirmButtonText: '移入回收站', cancelButtonText: '取消', type: 'warning' })
    history.locked = true
    await api.conversationRecord(auth.token, id, history.deleted)
    history.locked = false
    if (!history.deleted && route.query.conversation_id === id) await open()
    await history.load()
  } catch (failure) { if (failure !== 'cancel' && failure !== 'close') history.error = errorText(failure) }
  finally { history.locked = false }
}
const emit = defineEmits<{ selected: [] }>()
async function open(id?: string) {
  if (history.locked) return
  await router.push({ path: '/assistant', query: id ? { conversation_id: id } : { new: crypto.randomUUID() } })
  emit('selected')
}
</script>
<template>
  <section class="conversation-history" aria-label="本人会话">
    <button class="new-chat" :disabled="history.locked" @click="open()"><AppIcon name="edit" />新对话</button>
    <form class="history-search" @submit.prevent="history.load(true)"><input v-model="history.search" aria-label="搜索会话" placeholder="搜索会话标题" maxlength="100" /><button aria-label="搜索" :disabled="history.loading"><AppIcon name="search" /></button></form>
    <div class="history-heading"><button :disabled="history.locked" @click="history.deleted = !history.deleted; history.load(true)">{{ history.deleted ? '回收站 · 返回会话' : '最近会话 · 回收站' }}</button><button :disabled="history.loading" aria-label="刷新会话列表" @click="history.load()">刷新</button></div>
    <ErrorPanel :message="history.error" />
    <p v-if="history.loading" role="status">加载中</p>
    <p v-else-if="!history.items.length">{{ history.search ? '没有匹配的会话' : '尚无会话' }}</p>
    <div class="history-items"><div v-for="row in history.items" :key="row.conversation_id" class="history-row"><button class="history-open" :class="{ selected: route.query.conversation_id === row.conversation_id }" :disabled="history.locked || history.deleted" @click="open(row.conversation_id)"><span>{{ row.title }}</span><small>{{ row.revision }} 轮 · {{ timestamp(row.created_at) }}</small></button><button class="record-action" :disabled="history.locked" :aria-label="`${history.deleted ? '恢复' : '删除'}会话 ${row.title}`" @click="remove(row.conversation_id, row.title)">{{ history.deleted ? '恢复' : '删除' }}</button></div></div>
    <div v-if="history.total > 10" class="history-pages"><button :disabled="history.page <= 1 || history.loading" @click="history.page--; history.load()">上一页</button><span>{{ history.page }} / {{ Math.ceil(history.total / 10) }}</span><button :disabled="history.page * 10 >= history.total || history.loading" @click="history.page++; history.load()">下一页</button></div>
  </section>
</template>
<style scoped>
.conversation-history{display:flex;flex-direction:column;min-height:0;flex:1;font-size:12px}.new-chat{display:flex;align-items:center;gap:10px;border:1px solid #4b6d60;background:#24483b;color:#e8f2eb;padding:12px;border-radius:8px;text-align:left;cursor:pointer}.new-chat .app-icon{width:18px;height:18px}.history-search{display:flex;margin:14px 0;border:1px solid #426254;border-radius:7px;overflow:hidden}.history-search input{background:transparent;color:#edf5ee;min-width:0;width:100%;border:0;padding:10px;font:inherit;outline-offset:-2px}.history-search button{padding:8px;border:0;background:transparent;color:#b7ccbf;cursor:pointer}.history-search .app-icon{width:17px;height:17px}.history-heading{display:flex;justify-content:space-between;margin:8px 0;color:#a9bfb0}.history-heading button,.history-pages button{background:transparent;border:0;color:inherit;cursor:pointer;font:inherit}.history-items{min-height:0;overflow:auto;flex:1}.history-items button{display:block;text-align:left;border:0;border-radius:7px;background:transparent;color:#bdcec1;padding:12px 9px;width:100%;cursor:pointer;font:inherit}.history-items button:hover,.history-items button.selected{background:#2a4b3f;color:#fff}.history-items span{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.history-items small{display:block;color:#94aa9a;font-size:10px;margin-top:6px}.history-pages{display:flex;justify-content:space-between;margin:12px 0;font-size:11px}.legacy-link{display:block;border-top:1px solid #355547;padding:14px 4px;color:#bbd1c3}button:disabled{opacity:.5;cursor:default}
</style>

<style scoped>
.conversation-history{color:#4b6651}.new-chat{background:#e4eddd;color:#3f6244;border-color:#d6e3cc}.history-search{border-color:#d7e2cf}.history-search input{color:#3e5e46}.history-search button,.history-heading{color:#738769}.history-row{display:flex;align-items:center;border-radius:7px}.history-items .history-open{min-width:0;color:#4b6651;flex:1}.history-items .history-open:hover,.history-items .history-open.selected{background:#e4eddd;color:#35563d}.history-items .record-action{width:auto;flex:none;color:#798b73;padding:8px 4px;font-size:10px}.history-items small{color:#8b9b80}.history-pages{color:#77896e}
</style>
