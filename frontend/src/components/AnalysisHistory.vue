<script setup lang="ts">
import type { AgentRun } from '../types'
import { timestamp } from '../format'
import { reportState } from '../reportState'
import ErrorPanel from './ErrorPanel.vue'
defineProps<{ runs: AgentRun[]; total: number; page: number; currentId?: string; locked: boolean; loading: boolean; deleted: boolean; error: string }>()
const emit = defineEmits<{ open: [row: AgentRun]; fresh: []; refresh: []; page: [value: number]; trash: []; record: [row: AgentRun] }>()
const labels: Record<string, string> = { queued: '等待执行', running: '正在分析', waiting_review: '待人工审核', succeeded: '已保存', failed: '失败', cancelled: '已取消' }
function stateLabel(row: AgentRun) {
  return row.review ? reportState(row.review, row.job.status) : labels[row.job.status]
}
</script>
<template>
  <div class="analysis-records" aria-label="本人分析记录">
    <el-button type="primary" plain :disabled="locked" @click="emit('fresh')">新分析</el-button>
    <div class="history-title"><button class="history-switch" :disabled="locked || loading" @click="emit('trash')">{{ deleted ? '回收站 · 返回分析' : '最近分析 · 回收站' }}</button><el-button text :disabled="loading" @click="emit('refresh')">刷新</el-button></div>
    <ErrorPanel :message="error" />
    <p v-if="loading && !runs.length" role="status">读取记录中</p><p v-else-if="!runs.length">{{ deleted ? '回收站为空' : '尚无分析记录' }}</p>
    <div class="history-scroll"><div v-for="row in runs" :key="row.run_id" class="history-row"><button class="analysis-history" :class="{ selected: row.run_id === currentId }" :disabled="locked || deleted" @click="emit('open', row)"><span>{{ row.question }}</span><small>{{ stateLabel(row) }} · {{ timestamp(row.job.created_at) }}</small></button><button class="record-action" :disabled="locked || loading || (!deleted && ['queued', 'running', 'waiting_review'].includes(row.job.status))" :aria-label="`${deleted ? '恢复' : '删除'}分析 ${row.question}`" :title="!deleted && ['queued', 'running', 'waiting_review'].includes(row.job.status) ? '请先停止分析并等待结束' : undefined" @click="emit('record', row)">{{ deleted ? '恢复' : '删除' }}</button></div></div>
    <div v-if="total > 10" class="history-pages"><button :disabled="page <= 1 || loading || locked" @click="emit('page', page - 1)">上一页</button><span>{{ page }} / {{ Math.ceil(total / 10) }}</span><button :disabled="page * 10 >= total || loading || locked" @click="emit('page', page + 1)">下一页</button></div>
  </div>
</template>
<style scoped>
.analysis-records{display:flex;flex:1;flex-direction:column;min-height:0;gap:12px}.history-title{display:flex;justify-content:space-between;align-items:center;font-size:12px;color:#68825d}.history-scroll{flex:1;min-height:0;overflow:auto}.analysis-history{display:block;flex:1;min-width:0;text-align:left;border:0;border-radius:8px;background:transparent;padding:12px 10px;cursor:pointer;color:#355747;font:inherit}.analysis-history:hover,.analysis-history.selected{background:#e8efdf}.analysis-history span{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere;font-size:13px;line-height:1.8}.analysis-history small{display:block;margin-top:5px;color:#849479;font-size:10px}.analysis-history:disabled{cursor:default;opacity:.55}.history-pages{display:flex;justify-content:space-between;align-items:center;gap:8px;font-size:11px}.history-pages button{border:0;background:transparent;color:#64805b;cursor:pointer}.history-pages button:disabled{opacity:.45}.analysis-records>p{font-size:12px;color:#849479}
.history-row{display:flex;align-items:center}.history-switch{border:0;background:transparent;padding:0;color:inherit;font:inherit;cursor:pointer;text-align:left}.record-action{flex:none;border:0;background:transparent;color:#798b73;padding:8px 4px;font-size:10px;cursor:pointer}.record-action:disabled,.history-switch:disabled{opacity:.45;cursor:default}
</style>
