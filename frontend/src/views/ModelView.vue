<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '../api'
import { useAuthStore } from '../stores/auth'
import { cancelled, errorText, number } from '../format'
import type { Model } from '../types'
import ErrorPanel from '../components/ErrorPanel.vue'
const auth = useAuthStore(); const models = ref<Model[]>([]); const loading = ref(false); const error = ref(''); let controller: AbortController | undefined
async function load() {
  controller?.abort(); controller = new AbortController(); const signal = controller.signal
  loading.value = true; error.value = ''; models.value = []
  try { const data = await api.models(auth.token, signal); if (!signal.aborted) models.value = data.items }
  catch (failure) { if (!cancelled(failure)) error.value = errorText(failure) }
  finally { if (!signal.aborted) loading.value = false }
}
onMounted(load); onBeforeUnmount(() => controller?.abort())
</script>
<template>
  <div class="page-heading"><div><div class="eyebrow">MODEL & EVALUATION</div><h1>模型与验证</h1><p>查看已发布模型的真实配置、评估范围和产物指纹。</p></div><span class="context-badge">CPU / LSTM</span></div><ErrorPanel :message="error" retry @retry="load" />
  <div v-loading="loading"><div v-if="!models.length && !loading && !error" class="panel empty-state">没有已发布模型</div><section v-for="model in models" :key="model.model_version" class="panel model-panel"><div class="panel-heading"><div><h2>{{ model.name }} 剩余运行周期预测</h2><p class="mono">{{ model.model_version }}</p></div><el-tag type="success">已发布</el-tag></div><div class="model-specs"><div><span>训练轮数</span><strong>{{ model.configuration.completed_epochs }}</strong></div><div><span>最佳权重</span><strong>第 {{ model.configuration.best_epoch }} 轮</strong></div><div><span>输入窗口</span><strong>{{ model.configuration.window_size }} 周期</strong></div><div><span>有序特征</span><strong>{{ model.configuration.features.length }} 列</strong></div></div>
    <h3>固定配置后的离线评估</h3><el-table :data="[{ scope: '训练文件内的隔离验证窗口', ...model.metrics.validation }, { scope: '官方测试：每台设备末端窗口', ...model.metrics.official_test_terminal }]" class="evaluation-table"><el-table-column prop="scope" label="评估范围" min-width="230" /><el-table-column prop="samples" label="样本数" width="110" /><el-table-column label="MAE / 运行周期" min-width="160"><template #default="scope">{{ number(scope.row.mae, 6) }}</template></el-table-column><el-table-column label="RMSE / 运行周期" min-width="170"><template #default="scope">{{ number(scope.row.rmse, 6) }}</template></el-table-column></el-table>
    <p class="method-caption">按验证集原始 RMSE 选择最佳权重；官方测试仅在配置固定后用于离线评估。上述误差不表示单次预测的置信区间。</p>
    <h3>模型输入顺序</h3><div class="feature-list"><span v-for="(feature, index) in model.configuration.features" :key="feature"><b>{{ String(index + 1).padStart(2, '0') }}</b>{{ feature }}</span></div>
    <el-collapse class="artifact-details"><el-collapse-item title="查看训练配置与 SHA-256 指纹" name="artifacts"><dl class="detail-grid"><dt>训练配置</dt><dd><pre>{{ JSON.stringify(model.configuration.training, null, 2) }}</pre></dd><dt>部署清单</dt><dd class="mono">{{ model.manifest_sha256 }}</dd><dt>模型权重</dt><dd class="mono">{{ model.weight_sha256 }}</dd><dt>离线评估</dt><dd class="mono">{{ model.metrics.evaluation_sha256 }}</dd></dl></el-collapse-item></el-collapse>
  </section><div class="scope-note"><strong>使用限制</strong><p>当前结果来自 FD001、单次随机种子训练。没有验证跨数据集或现场设备泛化；线上不读取官方测试标签。风险分级采用演示业务阈值，需要人工判断维护建议。</p></div></div>
</template>
