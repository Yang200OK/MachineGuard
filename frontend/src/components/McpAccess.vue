<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref } from 'vue'
import { ElDrawer, ElCheckbox, ElCheckboxGroup, ElInput, ElInputNumber } from 'element-plus'
import { api, ApiError } from '../api'
import { useAuthStore } from '../stores/auth'
import { cancelled, errorText, timestamp } from '../format'
import type { McpConfig, McpCredential, McpGrant, McpActivity } from '../mcpTypes'
import ErrorPanel from './ErrorPanel.vue'

const auth = useAuthStore(); const opened = ref(false); const config = ref<McpConfig>()
const credentials = ref<McpCredential[]>([]); const activities = ref<McpActivity[]>([])
const error = ref(''); const notice = ref(''); const loading = ref(false); const busy = ref(false)
const credentialPage = ref(1); const credentialTotal = ref(0); const activityPage = ref(1); const activityTotal = ref(0)
const secret = ref(''); const pending = ref<McpGrant>(); const cacheInvalid = ref(false)
const form = reactive({ client_name: '', scopes: [] as string[], expires_minutes: 60 })
const key = `machineguard.mcp.issue.${auth.user?.id}`
const titles: Record<string, string> = { list_engines: '设备列表', get_engine_overview: '设备概况', get_sensor_trend: '传感器趋势', predict_rul: '可信预测（保存本人记录）', get_prediction_history: '本人预测历史', search_maintenance_knowledge: '资料候选检索（调用百炼）', search_evidence_paths: '证据路径', read_report: '授权报告读取' }
const states: Record<string, string> = { active: '有效', revoked: '已撤销', expired: '已到期', session_ended: '签发会话已结束' }
let controller: AbortController | undefined
const saved = sessionStorage.getItem(key)
if (saved) {
  try {
    const body = JSON.parse(saved)
    if (!body || typeof body.client_name !== 'string' || !Array.isArray(body.scopes) || !body.scopes.every((item: unknown) => typeof item === 'string') || !Number.isInteger(body.expires_minutes) || typeof body.idempotency_key !== 'string') throw new Error('MCP 原签发请求记录损坏，请先核对凭据列表。')
    pending.value = body; Object.assign(form, body)
  } catch (failure) { cacheInvalid.value = true; error.value = errorText(failure) }
}
const remoteConfig = computed(() => JSON.stringify({ mcpServers: { MachineGuard: { type: 'http', url: config.value?.resource_url, headers: { Authorization: `Bearer ${secret.value || '<独立 MCP 凭据>'}` } } } }, null, 2))
const stdioConfig = computed(() => JSON.stringify({ mcpServers: { MachineGuard: { command: config.value?.stdio_command, args: config.value?.stdio_args, env: { MACHINEGUARD_MCP_TOKEN: secret.value || '<独立 MCP 凭据>' } } } }, null, 2))
async function load() {
  controller?.abort(); controller = new AbortController(); const signal = controller.signal
  loading.value = true; if (!cacheInvalid.value) error.value = ''
  const results = await Promise.allSettled([api.mcpConfig(auth.token, signal), api.mcpCredentials(auth.token, (credentialPage.value - 1) * 20, signal), api.mcpActivity(auth.token, (activityPage.value - 1) * 20, signal)])
  if (signal.aborted) return
  const [settings, rows, logs] = results
  if (settings.status === 'fulfilled') config.value = settings.value
  if (rows.status === 'fulfilled') { credentials.value = rows.value.items; credentialTotal.value = rows.value.total }
  if (logs.status === 'fulfilled') { activities.value = logs.value.items; activityTotal.value = logs.value.total }
  for (const result of results) if (result.status === 'rejected' && !cancelled(result.reason)) error.value = errorText(result.reason)
  loading.value = false
}
async function issue() {
  if (busy.value || cacheInvalid.value) return
  busy.value = true; error.value = ''; notice.value = ''; secret.value = ''
  try {
    if (!pending.value) {
      const body = { client_name: form.client_name.trim(), scopes: [...form.scopes], expires_minutes: form.expires_minutes, idempotency_key: crypto.randomUUID() }
      // 先成功保存原参数再发送；本地存储失败时明确停止，避免刷新后丢失动作键。
      sessionStorage.setItem(key, JSON.stringify(body))
      pending.value = body
    }
    const result = await api.issueMcpCredential(auth.token, pending.value)
    pending.value = undefined; sessionStorage.removeItem(key)
    secret.value = result.access_token ?? ''
    notice.value = result.replayed ? '原请求已签发，凭据原值不会再次返回。可在列表撤销后重新签发。' : '凭据已签发，仅本次展示。关闭侧栏后将清除；退出签发会话后失效。'
    credentialPage.value = 1; await load()
  } catch (failure) {
    error.value = errorText(failure)
    if (failure instanceof ApiError && [401, 403, 409, 422, 429].includes(failure.status)) { pending.value = undefined; sessionStorage.removeItem(key) }
  } finally { busy.value = false }
}
async function revoke(id: string) {
  if (busy.value) return
  busy.value = true; error.value = ''
  try { await api.revokeMcpCredential(auth.token, id); secret.value = ''; notice.value = '凭据已撤销，后续 MCP 调用将被拒绝。'; await load() }
  catch (failure) { error.value = errorText(failure) }
  finally { busy.value = false }
}
async function copy(value: string) {
  try { await navigator.clipboard.writeText(value); notice.value = '已复制配置，请粘贴到可信客户端；其中的凭据代表所选访问权限。' }
  catch { error.value = '复制失败，请检查浏览器剪贴板权限。' }
}
function close() { controller?.abort(); secret.value = '' }
function clearBroken() { sessionStorage.removeItem(key); cacheInvalid.value = false; pending.value = undefined; error.value = ''; notice.value = '已清除损坏的本地签发参数，请重新填写。' }
onBeforeUnmount(() => { controller?.abort(); secret.value = '' })
</script>

<template>
  <section class="panel mcp-entry"><div><h2>MCP 外部接入</h2><p class="method-caption">让支持 MCP 的可信客户端调用所选能力，接入可随时撤销。</p></div><el-button @click="opened = true; load()">管理接入</el-button></section>
  <el-drawer v-model="opened" title="MCP 外部接入" size="min(760px, 100vw)" :close-on-click-modal="!busy" :close-on-press-escape="!busy" :show-close="!busy" @closed="close">
    <ErrorPanel :message="error" /><p v-if="notice" class="scope-note" role="status">{{ notice }}</p>
    <p class="method-caption">凭据绑定当前登录会话，退出或到期即失效。只授权所选能力，报告和历史仍检查归属。</p>
    <form class="mcp-form" @submit.prevent="issue"><label>客户端名称<el-input v-model="form.client_name" aria-label="MCP 客户端名称" maxlength="64" :disabled="busy || !!pending" placeholder="例如：我的本地 MCP 客户端" /></label><label>有效分钟（不超过登录会话）<el-input-number v-model="form.expires_minutes" aria-label="MCP 凭据有效分钟" :min="5" :max="1440" :disabled="busy || !!pending" /></label>
      <fieldset><legend>允许调用的能力</legend><el-checkbox-group v-model="form.scopes" :disabled="busy || !!pending"><el-checkbox v-for="tool in config?.tools" :key="tool.name" :value="tool.name">{{ titles[tool.name] }}</el-checkbox></el-checkbox-group></fieldset>
      <p v-if="pending" class="method-caption">上次签发结果待确认，只重发原请求键，不会另建凭据。</p><el-button type="primary" native-type="submit" :loading="busy" :disabled="cacheInvalid || (!pending && (!form.client_name.trim() || !form.scopes.length))">{{ pending ? '确认原签发请求' : '签发独立凭据' }}</el-button><el-button v-if="cacheInvalid" @click="clearBroken">已核对列表，清除损坏参数</el-button>
    </form>
    <section v-if="secret" class="mcp-secret"><h3>本次凭据</h3><el-input :model-value="secret" type="password" show-password readonly aria-label="本次 MCP 凭据" /><p class="method-caption">不放入网址、代码仓库或日志；只配置到你信任的客户端。</p></section>
    <details v-if="config" class="mcp-section"><summary>客户端配置</summary><p class="method-caption">Streamable HTTP 地址：{{ config.resource_url }}。以下为常见配置结构，客户端字段以其说明为准；凭据通过请求头发送。</p><pre>{{ remoteConfig }}</pre><el-button :disabled="!secret" @click="copy(remoteConfig)">复制 HTTP 配置</el-button><details><summary>本地 stdio 配置</summary><p class="method-caption">只适用于安装了本项目的电脑；Python 和脚本须为本机绝对路径。</p><pre>{{ stdioConfig }}</pre><el-button :disabled="!secret" @click="copy(stdioConfig)">复制 stdio 配置</el-button></details><p class="method-caption">每凭据每分钟最多 {{ config.max_calls_per_minute }} 次操作。资料检索会调用百炼，预测会保存本人记录。</p></details>
    <section class="mcp-section" v-loading="loading"><div class="mcp-title"><h3>本人凭据</h3><el-button text :disabled="busy" @click="load">刷新</el-button></div><p v-if="!credentials.length">暂无凭据。</p><article v-for="item in credentials" :key="item.credential_id" class="mcp-record"><strong>{{ item.client_name }}</strong><span>{{ states[item.status] || item.status }}</span><p>到期 {{ timestamp(item.expires_at) }}</p><p class="method-caption">{{ item.scopes.map(name => titles[name]).join('、') }}</p><el-button v-if="item.status === 'active'" :disabled="busy" @click="revoke(item.credential_id)">撤销接入</el-button></article><el-pagination v-if="credentialTotal > 20" v-model:current-page="credentialPage" :page-size="20" :total="credentialTotal" layout="prev, pager, next" @current-change="load" /></section>
    <details class="mcp-section"><summary>本人调用记录</summary><p v-if="!activities.length">暂无真实调用记录。</p><article v-for="item in activities" :key="item.request_id" class="mcp-record"><strong>{{ item.client_name }} · {{ titles[item.operation] || item.method || item.operation }}</strong><span>{{ item.status === 'succeeded' ? '成功' : '失败' }}</span><p>{{ timestamp(item.created_at) }} · {{ item.transport }} · {{ item.duration_ms }} 毫秒</p><p v-if="item.error_code">{{ item.error_code }}</p><details><summary>请求定位编号</summary><p class="mono">{{ item.request_id }}</p></details></article><el-pagination v-if="activityTotal > 20" v-model:current-page="activityPage" :page-size="20" :total="activityTotal" layout="prev, pager, next" @current-change="load" /></details>
  </el-drawer>
</template>

<style scoped>
.mcp-entry{margin-top:20px;display:flex;align-items:center;justify-content:space-between;gap:16px}.mcp-entry h2{font-size:17px}.mcp-form{display:grid;gap:16px;margin:22px 0}.mcp-form>label{display:grid;gap:8px;font-size:13px}.mcp-form fieldset{border:1px solid #dce5df;padding:14px;border-radius:8px;display:grid;gap:8px;min-width:0}.mcp-form legend{font-size:13px}.mcp-form .el-checkbox-group{display:grid;gap:12px}.mcp-form .el-checkbox{height:auto;white-space:normal}.mcp-form :deep(.el-checkbox__label){white-space:normal}.mcp-form>.el-button{justify-self:start;margin:0}.mcp-section{border-top:1px solid #e3e9e1;padding:18px 0;font-size:13px;overflow-wrap:anywhere}.mcp-section summary{cursor:pointer;line-height:1.9}.mcp-section pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f8f1;padding:14px;font-size:12px;margin:12px 0}.mcp-title{display:flex;justify-content:space-between;align-items:center}.mcp-record{padding:14px 0;border-bottom:1px solid #e3e9e1;line-height:1.9}.mcp-record>span{float:right;margin-left:8px;font-size:12px}.mcp-secret{margin:18px 0}.mcp-secret h3{margin-bottom:10px}.mcp-section .el-pagination{max-width:100%}
</style>
