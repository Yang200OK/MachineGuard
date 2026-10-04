<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '../api'
import { useAuthStore } from '../stores/auth'
import { cancelled, engineLabel, errorText, number } from '../format'
import type { Engine, Model } from '../types'
import AppIcon from '../components/AppIcon.vue'
import ErrorPanel from '../components/ErrorPanel.vue'

const auth = useAuthStore(); const engines = ref<Engine[]>([]); const total = ref(0); const models = ref<Model[]>([])
const loading = ref(false); const error = ref(''); const model = computed(() => models.value[0]); let controller: AbortController | undefined
async function load() {
  controller?.abort(); controller = new AbortController(); const signal = controller.signal
  loading.value = true; error.value = ''; engines.value = []; models.value = []
  try {
    const [devices, registry] = await Promise.all([api.engines(auth.token, 0, 6, signal), api.models(auth.token, signal)])
    if (signal.aborted) return
    engines.value = devices.items; total.value = devices.total; models.value = registry.items
  } catch (failure) { if (!cancelled(failure)) error.value = errorText(failure) }
  finally { if (!signal.aborted) loading.value = false }
}
onMounted(load); onBeforeUnmount(() => controller?.abort())
</script>
<template>
  <div class="page-heading"><div><div class="eyebrow">OPERATIONS OVERVIEW</div><h1>运行总览</h1><p>从观测窗口出发，查看设备变化与可追溯的剩余寿命预测。</p></div><span class="context-badge">NASA 模拟数据</span></div>
  <ErrorPanel :message="error" retry @retry="load" />
  <div v-loading="loading">
    <section class="overview-hero"><div><span class="hero-tag">设备健康观察</span><h2>把变化看清，<br />把预测留存。</h2><p>选择发动机和当前周期，比较传感器趋势，<br class="desktop-only" />用已训练的 LSTM 生成可信预测并查看个人历史。</p><RouterLink to="/devices" class="hero-link">进入设备工作台<AppIcon name="arrow" /></RouterLink></div><div class="hero-diagram" aria-hidden="true"><div class="diagram-label">CONTINUOUS OBSERVATION</div><div class="diagram-bars"><i v-for="i in 27" :key="i" :style="{ height: `${28 + ((i * 17) % 63)}%` }" /></div><div class="diagram-axis"><span>原始观测</span><strong>30 周期窗口</strong><span>CPU 预测</span></div></div></section>
    <section class="metric-grid" aria-label="运行指标"><div class="metric-card"><span>可访问设备</span><strong>{{ total }}<small>台</small></strong><p>{{ auth.user?.is_guest ? '当前访客开放示例设备' : '当前账号授权的设备范围' }}</p></div><div class="metric-card"><span>正式训练</span><strong>{{ model ? model.configuration.completed_epochs : '—' }}<small>轮</small></strong><p v-if="model">最佳权重来自第 {{ model.configuration.best_epoch }} 轮</p></div><div class="metric-card"><span>验证集 RMSE</span><strong>{{ model ? number(model.metrics.validation.rmse, 2) : '—' }}<small>周期</small></strong><p v-if="model">{{ model.metrics.validation.samples.toLocaleString() }} 个隔离验证窗口</p></div><div class="metric-card"><span>官方测试 RMSE</span><strong>{{ model ? number(model.metrics.official_test_terminal.rmse, 2) : '—' }}<small>周期</small></strong><p v-if="model">{{ model.metrics.official_test_terminal.samples }} 台设备的末端窗口</p></div></section>
    <div class="overview-grid"><section class="panel"><div class="panel-heading"><div><h2>从一台设备开始</h2><p>当前权限范围内的观测设备</p></div><RouterLink to="/devices" class="text-link">查看设备<AppIcon name="arrow" /></RouterLink></div><div v-if="!engines.length && !loading" class="empty-state">没有可显示的设备</div><div v-for="engine in engines" :key="engine.engine_key" class="preview-device"><span class="engine-symbol"><AppIcon name="devices" /></span><div><strong>{{ engineLabel(engine.engine_id) }}</strong><small>FD001 / test · {{ engine.is_demo ? '示例设备' : '模拟设备' }}</small></div><span class="preview-cycle">观测到 <strong>{{ engine.last_observed_cycle }}</strong> 周期</span></div></section>
    <section class="panel method-panel"><div class="eyebrow">HOW IT WORKS</div><h2>每一次结果，都能回到输入</h2><ol class="workflow-list"><li><span>01</span><div><strong>固定观测边界</strong><p>以选定周期为末端，曲线和窗口使用同一边界。</p></div></li><li><span>02</span><div><strong>计算剩余运行周期</strong><p>CPU LSTM 复用训练期标准化参数与有序特征。</p></div></li><li><span>03</span><div><strong>留存可信结果</strong><p>保存窗口、模型版本与输入哈希，历史按本人隔离。</p></div></li></ol><RouterLink to="/model" class="text-link">查看模型依据<AppIcon name="arrow" /></RouterLink></section></div>
    <div class="scope-note"><strong>观察边界</strong><p>FD001 是模拟数据。RUL 表示剩余运行周期，不能换算成天数；传感器变化与低 RUL 不能单独证明具体部件故障。</p></div>
  </div>
</template>
