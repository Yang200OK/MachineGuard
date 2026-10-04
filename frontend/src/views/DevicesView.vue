<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import type { EChartsOption } from 'echarts'
import { api } from '../api'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { useSelectionStore } from '../stores/selection'
import { cancelled, engineLabel, errorText, number, riskLabel, timestamp } from '../format'
import type { Engine, Model, Prediction, SensorResponse } from '../types'
import AppIcon from '../components/AppIcon.vue'
import ErrorPanel from '../components/ErrorPanel.vue'
import LineChart from '../components/LineChart.vue'

const router = useRouter(); const auth = useAuthStore(); const selection = useSelectionStore()
const engines = ref<Engine[]>([]); const total = ref(0); const enginePage = ref(1); const enginesLoading = ref(false)
const models = ref<Model[]>([]); const modelVersion = ref(''); const engine = ref<Engine | null>(null)
const draftCycle = ref(1); const endCycle = ref(1); const range = ref('window'); const columns = ref(['sensor_2', 'sensor_3'])
const sensors = ref<SensorResponse | null>(null); const sensorsLoading = ref(false); const sensorError = ref('')
const history = ref<Prediction[]>([]); const historyTotal = ref(0); const historyPage = ref(1); const historyLoading = ref(false); const historyError = ref('')
const prediction = ref<Prediction | null>(null); const predicting = ref(false); const predictionError = ref(''); const pageError = ref('')
let engineController: AbortController | undefined; let sensorController: AbortController | undefined; let historyController: AbortController | undefined; let disposed = false
const model = computed(() => models.value.find(value => value.model_version === modelVersion.value))
const windowSize = computed(() => model.value?.configuration.window_size ?? 0)
const startCycle = computed(() => range.value === 'all' ? 1 : Math.max(1, endCycle.value - windowSize.value + 1))
const sensorFields = [...Array.from({ length: 21 }, (_, i) => `sensor_${i + 1}`), ...Array.from({ length: 3 }, (_, i) => `setting_${i + 1}`)]
const canPredict = computed(() => !!engine.value && !!model.value && endCycle.value >= windowSize.value && !!sensors.value && !sensorsLoading.value && !predicting.value)

async function loadEngines() {
  engineController?.abort(); engineController = new AbortController(); const signal = engineController.signal
  enginesLoading.value = true; pageError.value = ''; engines.value = []
  try {
    const result = await api.engines(auth.token, (enginePage.value - 1) * 20, 20, signal)
    if (signal.aborted) return
    engines.value = result.items; total.value = result.total
  } catch (failure) { if (!cancelled(failure)) pageError.value = errorText(failure) }
  finally { if (!signal.aborted) enginesLoading.value = false }
}
async function initialize() {
  pageError.value = ''; engine.value = null
  try {
    selection.restore()
    enginePage.value = selection.selected?.page ?? 1
    const [registry] = await Promise.all([api.models(auth.token), loadEngines()])
    if (disposed) return
    models.value = registry.items
    const firstModel = registry.items[0]
    if (!firstModel) throw new Error('没有已发布模型，暂时不能进入预测工作台。')
    modelVersion.value = firstModel.model_version
    if (pageError.value) return
    const saved = selection.selected
    const initial = saved ? engines.value.find(e => e.engine_key === saved.engineKey) : engines.value[0]
    if (saved && !initial) throw new Error('保存的设备已不在当前授权范围，请清除选择并重新选择。')
    if (initial) {
      const cycle = saved?.endCycle ?? initial.last_observed_cycle
      if (!Number.isInteger(cycle) || cycle < 1 || cycle > initial.last_observed_cycle) throw new Error('保存的周期不在该设备观测范围，请清除选择。')
      engine.value = initial; draftCycle.value = cycle; endCycle.value = cycle
      selection.save(initial.engine_key, cycle, enginePage.value)
      await loadObservation()
    }
  } catch (failure) { if (!disposed) pageError.value = errorText(failure) }
}
async function clearSelection() { selection.clear(); await initialize() }
async function chooseEngine(value: Engine) {
  if (predicting.value) return
  engine.value = value; draftCycle.value = value.last_observed_cycle; endCycle.value = value.last_observed_cycle
  selection.save(value.engine_key, value.last_observed_cycle, enginePage.value)
  await loadObservation()
}
async function applyCycle() {
  if (predicting.value || !engine.value) return
  if (!Number.isInteger(draftCycle.value) || draftCycle.value < 1 || draftCycle.value > engine.value.last_observed_cycle) {
    sensorError.value = '请选择观测范围内的整数周期。'; return
  }
  endCycle.value = draftCycle.value
  selection.save(engine.value.engine_key, endCycle.value, selection.selected?.page ?? enginePage.value)
  await loadObservation()
}
async function loadSensors() {
  sensorController?.abort(); sensorController = new AbortController(); const signal = sensorController.signal
  sensors.value = null; sensorError.value = ''; sensorsLoading.value = true
  if (!engine.value || !model.value || !columns.value.length) {
    sensorError.value = '请选择设备、模型和至少一个观测字段。'; sensorsLoading.value = false; return
  }
  try {
    const result = await api.sensors(auth.token, engine.value.engine_key, startCycle.value, endCycle.value, columns.value, signal)
    if (!signal.aborted) sensors.value = result
  } catch (failure) { if (!cancelled(failure)) sensorError.value = errorText(failure) }
  finally { if (!signal.aborted) sensorsLoading.value = false }
}
async function loadHistory() {
  historyController?.abort(); historyController = new AbortController(); const signal = historyController.signal
  history.value = []; historyTotal.value = 0; historyError.value = ''; historyLoading.value = true
  if (!engine.value) { historyLoading.value = false; return }
  try {
    const result = await api.history(auth.token, engine.value.engine_key, endCycle.value, (historyPage.value - 1) * 10, 10, signal)
    if (signal.aborted) return
    history.value = result.items; historyTotal.value = result.total
    if (!prediction.value) prediction.value = result.items.find(row => row.end_cycle === endCycle.value && row.model_version === modelVersion.value) ?? null
  } catch (failure) { if (!cancelled(failure)) historyError.value = errorText(failure) }
  finally { if (!signal.aborted) historyLoading.value = false }
}
async function loadObservation() {
  // 设备/周期/模型变化时先清除旧结论，迟到的查询通过 AbortController 丢弃。
  prediction.value = null; predictionError.value = ''; historyPage.value = 1
  await Promise.all([loadSensors(), loadHistory()])
}
async function predict() {
  if (!canPredict.value || !engine.value) return
  predicting.value = true; predictionError.value = ''
  const key = engine.value.engine_key; const cycle = endCycle.value; const version = modelVersion.value
  try {
    // 同一设备/周期/模型保留请求键，网络超时或刷新后可确认原结果而非重复写入。
    const result = await api.predict(auth.token, selection.attempt(version))
    if (disposed || engine.value?.engine_key !== key || endCycle.value !== cycle || modelVersion.value !== version) return
    prediction.value = result; historyPage.value = 1
    await loadHistory()
  } catch (failure) { if (!disposed) predictionError.value = errorText(failure) }
  finally { predicting.value = false }
}

const colors = ['#188369', '#6488b6']
const sensorOptions = computed<EChartsOption>(() => {
  const data = sensors.value
  if (!data) return {}
  return {
    animation: false, color: colors, aria: { enabled: true },
    grid: { left: 65, right: data.trends.length > 1 ? 85 : 30, top: 48, bottom: 75 },
    tooltip: { trigger: 'axis', confine: true }, legend: { bottom: 0, icon: 'roundRect' },
    xAxis: { type: 'category', name: '周期', boundaryGap: false, data: data.records.map(row => row.cycle), axisLine: { lineStyle: { color: '#cbd7d1' } }, axisLabel: { color: '#687c75' } },
    yAxis: data.trends.map((trend, index) => ({ type: 'value' as const, name: trend.field, scale: true, position: index === 0 ? 'left' as const : 'right' as const, axisLabel: { color: colors[index] }, splitLine: { show: index === 0, lineStyle: { color: '#edf1ef', type: 'dashed' as const } } })),
    dataZoom: [{ type: 'inside' }],
    series: data.trends.map((trend, index) => ({ name: trend.field, type: 'line' as const, yAxisIndex: index, data: data.records.map(row => row[trend.field]), showSymbol: data.records.length === 1, symbolSize: 6, smooth: false, lineStyle: { width: 2 } })),
  }
})
const historyOptions = computed<EChartsOption>(() => {
  const rows = history.value.filter(row => row.model_version === modelVersion.value).slice().sort((a, b) => a.end_cycle - b.end_cycle)
  return { animation: false, color: ['#188369', '#83988e'], aria: { enabled: true }, grid: { left: 60, right: 30, top: 30, bottom: 55 },
    tooltip: { trigger: 'axis', confine: true }, legend: { bottom: 0 },
    xAxis: { type: 'category', name: '周期', data: rows.map(row => row.end_cycle), axisLabel: { color: '#687c75' } },
    yAxis: { type: 'value', name: 'RUL / 运行周期', splitLine: { lineStyle: { color: '#edf1ef' } } },
    series: [{ name: '业务 RUL', type: 'line', data: rows.map(row => row.business_rul), showSymbol: true, symbolSize: 7, smooth: false }, { name: '原始 RUL', type: 'line', data: rows.map(row => row.raw_rul), showSymbol: true, symbolSize: 4, lineStyle: { type: 'dashed' }, smooth: false }],
  }
})
const hasCurrentModelHistory = computed(() => history.value.some(row => row.model_version === modelVersion.value))
onMounted(initialize)
onBeforeUnmount(() => { disposed = true; engineController?.abort(); sensorController?.abort(); historyController?.abort() })
</script>
<template>
  <div class="page-heading"><div><div class="eyebrow">DEVICE WORKSPACE</div><h1>设备工作台</h1><p>选择一台发动机，在固定周期边界下查看观测、预测和个人历史。</p></div><span class="context-badge">FD001 / TEST</span></div>
  <ErrorPanel :message="pageError" retry @retry="initialize" /><el-button v-if="pageError" class="clear-selection" @click="clearSelection">清除保存的选择</el-button>
  <div class="device-layout">
    <aside class="panel device-list"><div class="panel-heading"><div><h2>设备列表</h2><p>{{ total }} 台可访问设备</p></div><el-tag v-if="auth.user?.is_guest" type="info" size="small">访客</el-tag></div><div v-loading="enginesLoading" class="device-list-body"><button v-for="item in engines" :key="item.engine_key" type="button" class="device-choice" :class="{ selected: engine?.engine_key === item.engine_key }" :disabled="predicting" :aria-pressed="engine?.engine_key === item.engine_key" :aria-label="`选择${engineLabel(item.engine_id)}`" @click="chooseEngine(item)"><span class="engine-symbol"><AppIcon name="devices" /></span><span><strong>{{ engineLabel(item.engine_id) }}</strong><small>观测 1–{{ item.last_observed_cycle }} 周期</small></span><span class="selection-dot" /></button><div v-if="!engines.length && !enginesLoading" class="empty-state">没有可显示的设备</div></div><el-pagination v-if="total > 20" v-model:current-page="enginePage" :page-size="20" :total="total" layout="prev, pager, next" :pager-count="5" size="small" :disabled="predicting" @current-change="loadEngines" /><p class="list-foot">{{ auth.user?.is_guest ? '访客仅开放示例，历史归当前身份。' : '设备按原始发动机编号分页。' }}</p></aside>
    <div v-if="engine" class="device-main">
      <section class="panel selection-panel"><div class="device-title"><div><span class="mini-label">当前设备</span><h2>{{ engineLabel(engine.engine_id) }}</h2></div><el-button v-if="auth.user?.permissions.includes('analysis:create')" @click="router.push({ path: '/assistant', query: { mode: 'analysis', workflow: 'one_click' } })">一键分析</el-button><div class="device-identity"><span>{{ engine.source }}</span><small class="mono">数据版本 {{ engine.data_version.slice(0, 12) }}</small></div></div><div class="cycle-toolbar"><div class="cycle-input"><label for="end-cycle">分析末端周期</label><el-input-number id="end-cycle" v-model="draftCycle" :min="1" :max="engine.last_observed_cycle" :step="1" :precision="0" :disabled="predicting" aria-label="分析末端周期" @keyup.enter="applyCycle" /><el-button :disabled="predicting" @click="applyCycle">应用周期</el-button></div><div class="cycle-boundary">观测范围 <strong>1–{{ engine.last_observed_cycle }}</strong><span>当前边界 <b>{{ endCycle }}</b></span></div></div><el-slider v-model="draftCycle" :min="1" :max="engine.last_observed_cycle" :step="1" :disabled="predicting" aria-label="选择分析周期" @change="applyCycle" /><p class="method-caption">仅使用截至周期 {{ endCycle }} 的观测。模型窗口 {{ windowSize }} 周期；{{ endCycle >= windowSize ? `当前输入 ${endCycle - windowSize + 1}–${endCycle}` : '当前窗口不足，暂不能预测' }}。</p></section>
      <div class="analysis-grid"><section class="panel sensor-panel"><div class="panel-heading"><div><h2>传感器观测</h2><p>{{ startCycle }}–{{ endCycle }} 周期 · 原始数值</p></div><el-radio-group v-model="range" size="small" @change="loadSensors"><el-radio-button value="window">最近窗口</el-radio-button><el-radio-button value="all">截至当前全部</el-radio-button></el-radio-group></div><div class="sensor-picker"><label for="sensor-fields">比较字段</label><el-select id="sensor-fields" v-model="columns" multiple :multiple-limit="2" placeholder="选择 1–2 个字段" aria-label="比较传感器字段" @change="loadSensors"><el-option v-for="field in sensorFields" :key="field" :label="field" :value="field" /></el-select><span>独立纵轴 · 单位未知</span></div><ErrorPanel :message="sensorError" retry @retry="loadSensors" /><div v-loading="sensorsLoading" class="chart-shell"><LineChart v-if="sensors" :options="sensorOptions" :label="`${engineLabel(engine.engine_id)} 周期 ${sensors.start_cycle} 至 ${sensors.end_cycle} 的传感器曲线`" /><div v-else class="empty-state">{{ sensorsLoading ? '正在读取原始观测' : '等待有效观测数据' }}</div></div><div v-if="sensors" class="trend-summaries"><div v-for="trend in sensors.trends" :key="trend.field" class="trend-summary"><div><strong>{{ trend.field }}</strong><span class="observation-pill">{{ trend.observation }}</span></div><dl><dt>均值</dt><dd>{{ number(trend.mean) }}</dd><dt>首末变化</dt><dd>{{ number(trend.change) }}</dd><dt>斜率 / 周期</dt><dd>{{ number(trend.slope_per_cycle, 6) }}</dd><dt>相对变化</dt><dd>{{ trend.relative_change === null ? '未定义（首值为零）' : `${number(trend.relative_change * 100, 3)}%` }}</dd></dl></div></div><p class="method-caption">最小二乘斜率与首末变化可能方向不同。传感器变化是观测，不能单独证明部件故障。</p></section>
      <section class="panel prediction-panel"><div class="panel-heading"><div><h2>剩余运行周期</h2><p>可信预测 · CPU LSTM · <router-link to="/model">模型说明</router-link></p></div><span class="model-dot" /></div><label class="mini-label" for="model-version">已发布模型</label><el-select id="model-version" v-model="modelVersion" :disabled="predicting" aria-label="预测模型版本" @change="loadObservation"><el-option v-for="entry in models" :key="entry.model_version" :label="entry.model_version" :value="entry.model_version" /></el-select><ErrorPanel :message="predictionError" />
        <div v-if="prediction" class="prediction-result"><div class="result-caption">{{ prediction.replayed === true ? '已确认已有预测' : prediction.replayed === false ? '预测已保存' : '已保存的个人预测' }}</div><div class="rul-number">{{ number(prediction.business_rul, 2) }}</div><div class="rul-unit">{{ prediction.rul_unit }}</div><span :class="['risk-badge', prediction.risk_level]">{{ riskLabel[prediction.risk_level] }}</span><dl class="prediction-facts"><dt>原始 RUL</dt><dd>{{ number(prediction.raw_rul, 6) }}</dd><dt>输入窗口</dt><dd>{{ prediction.window_start_cycle }}–{{ prediction.end_cycle }}</dd><dt>推理耗时</dt><dd>{{ number(prediction.duration_ms, 2) }} ms</dd></dl><p class="risk-explanation">阈值：≤{{ prediction.risk_rule.critical_max }} 高风险关注，≤{{ prediction.risk_rule.warning_max }} 加强关注。{{ prediction.risk_rule.limitation }}</p></div><div v-else class="prediction-empty"><AppIcon name="model" /><h3>{{ endCycle < windowSize ? '窗口尚未满足' : '准备生成预测' }}</h3><p>{{ endCycle < windowSize ? `至少需要 ${windowSize} 个连续周期，请选择更晚的已观测周期。` : '结果由模型计算，保存后可查看完整窗口与输入指纹。' }}</p></div>
        <el-button type="primary" class="full-width" size="large" :loading="predicting" :disabled="!canPredict" @click="predict">{{ prediction ? '确认当前预测' : '生成并保存预测' }}</el-button><p class="prediction-foot">相同设备、周期与模型重复提交会确认原记录。</p><el-collapse v-if="prediction"><el-collapse-item title="查看预测来源" name="provenance"><dl class="detail-grid compact"><dt>预测编号</dt><dd class="mono">{{ prediction.prediction_id }}</dd><dt>模型版本</dt><dd class="mono">{{ prediction.model_version }}</dd><dt>输入 SHA-256</dt><dd class="mono">{{ prediction.input_sha256 }}</dd><dt>后处理</dt><dd class="mono">{{ prediction.postprocessing }}</dd><dt>风险规则</dt><dd class="mono">{{ prediction.risk_rule.version }}</dd><dt>生成时间</dt><dd>{{ timestamp(prediction.created_at) }}</dd><dt>首次请求</dt><dd class="mono">{{ prediction.request_id }}</dd></dl></el-collapse-item></el-collapse>
      </section></div>
      <section class="panel history-panel"><div class="panel-heading"><div><h2>个人预测历史</h2><p>仅包含本人在当前设备、截至周期 {{ endCycle }} 的记录</p></div><el-button size="small" :loading="historyLoading" @click="loadHistory">刷新历史</el-button></div><ErrorPanel :message="historyError" retry @retry="loadHistory" /><div v-loading="historyLoading"><LineChart v-if="hasCurrentModelHistory" :options="historyOptions" :label="`${engineLabel(engine.engine_id)} 当前页模型的历史 RUL，截止周期 ${endCycle}`" class="history-chart" /><p v-if="hasCurrentModelHistory" class="method-caption">曲线展示当前页、所选模型的记录，按周期排序；相同原始/业务 RUL 会重合。表格包含全部模型版本。</p><el-table :data="history" empty-text="当前设备与周期下暂无个人预测" class="history-table"><el-table-column label="生成时间" min-width="165"><template #default="scope">{{ timestamp(scope.row.created_at) }}</template></el-table-column><el-table-column prop="end_cycle" label="末端周期" width="100" /><el-table-column label="业务 RUL" width="110"><template #default="scope">{{ number(scope.row.business_rul, 3) }}</template></el-table-column><el-table-column label="原始 RUL" width="110"><template #default="scope">{{ number(scope.row.raw_rul, 3) }}</template></el-table-column><el-table-column label="关注等级" min-width="115"><template #default="scope"><span :class="['risk-badge', scope.row.risk_level]">{{ riskLabel[scope.row.risk_level as Prediction['risk_level']] }}</span></template></el-table-column><el-table-column prop="model_version" label="模型版本" min-width="240" /><el-table-column prop="prediction_id" label="预测编号" min-width="295" /></el-table><el-pagination v-if="historyTotal > 10" v-model:current-page="historyPage" :page-size="10" :total="historyTotal" layout="total, prev, pager, next" @current-change="loadHistory" /></div></section>
    </div><section v-else class="panel empty-state"><h2>请选择设备</h2><p>从左侧授权列表选择设备，建立当前观测边界。</p></section>
  </div>
</template>
