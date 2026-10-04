<script setup lang="ts">
import { computed, ref } from 'vue'
import { api } from '../api'
import { useAuthStore } from '../stores/auth'
import { errorText } from '../format'
import type { PublishKnowledgeRequest } from '../types'
import ErrorPanel from './ErrorPanel.vue'

const props = defineProps<{ versionId: string; parseId: string; revision: number; approved: boolean; active: boolean; archived?: boolean; published?: boolean }>()
const emit = defineEmits<{ changed: [] }>()
const auth = useAuthStore(); const busy = ref(false); const error = ref(''); const notice = ref(''); const jobId = ref('')
const storageKey = `machineguard.knowledge-publish.${auth.user?.id}.${props.versionId}`
const pending = ref<PublishKnowledgeRequest | null>(null); const invalid = ref(false)
try {
  const raw = sessionStorage.getItem(storageKey)
  if (raw) {
    const value = JSON.parse(raw)
    if (!value || !Number.isInteger(value.expected_revision) || value.expected_revision < 0 ||
      !/^[0-9a-f-]{36}$/i.test(value.parse_id) || !/^[0-9a-f-]{36}$/i.test(value.idempotency_key) ||
      Object.keys(value).sort().join(',') !== 'expected_revision,idempotency_key,parse_id') throw new Error('记录非法')
    pending.value = value
  }
} catch { invalid.value = true }
const ready = computed(() => !props.archived && !props.published && props.active && props.approved && !busy.value && !pending.value && !invalid.value)
function clear() { sessionStorage.removeItem(storageKey); pending.value = null; invalid.value = false }
async function publish(body?: PublishKnowledgeRequest) {
  if (props.archived || busy.value || (!body && !ready.value)) return
  busy.value = true; error.value = ''; notice.value = ''
  const request = body ?? { parse_id: props.parseId, expected_revision: props.revision, idempotency_key: crypto.randomUUID() }
  try {
    // 先保存固定审核依据；刷新后确认原请求，避免重复创建收费向量任务。
    sessionStorage.setItem(storageKey, JSON.stringify(request)); pending.value = request
    const result = await api.publishKnowledge(auth.token, props.versionId, request)
    clear(); jobId.value = result.owner_id === auth.user?.id && !result.job_deleted ? result.job_id : ''
    notice.value = result.replayed ? '已确认原发布任务。' : result.published_at ? '当前有效知识快照已包含此审核结果。' : '索引任务已提交，请到后台任务查看结果；成功后才发布知识快照。'
    emit('changed')
  } catch (failure) { error.value = errorText(failure) }
  finally { busy.value = false }
}
</script>

<template>
  <div class="publish-actions">
    <p v-if="archived">已归档资料仅供阅读。请先在资料库恢复，再核对审核与发布状态。</p><p v-else-if="published">此解析结果已用于当前问答，无需重复发布。</p><p v-else-if="approved">审核已通过。发布会将已审核切片发送到百炼建立索引，产生模型用量；成功后才用于问答。</p><p v-else>先核对正文与出处并通过审核，再发布用于问答。</p>
    <ErrorPanel :message="error" /><p v-if="notice" role="status">{{ notice }}</p>
    <el-button v-if="!archived && !published && approved" type="primary" :disabled="!ready" :loading="busy" @click="publish()">发布用于问答</el-button>
    <el-button v-if="pending" :disabled="busy || archived" @click="publish(pending)">确认原发布请求</el-button>
    <el-button v-if="pending || invalid" :disabled="busy" @click="clear">清除本页请求记录</el-button>
    <p v-if="pending">原请求仍待确认，当前操作会保持原解析和修订号。</p>
    <p v-if="invalid">本页请求记录无效，请清除记录后重新核对。</p>
    <router-link v-if="jobId" :to="{ path: '/jobs', query: { job_id: jobId } }">查看索引任务</router-link>
  </div>
</template>
<style scoped>.publish-actions{border-top:1px solid var(--el-border-color);margin:16px 0;padding:12px 0}.publish-actions p{line-height:1.8;overflow-wrap:anywhere}.publish-actions .el-button{margin:8px 8px 8px 0}</style>
