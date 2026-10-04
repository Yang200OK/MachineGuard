<script setup lang="ts">
import type { ReportCandidate, FinalReport } from '../types'
import { engineLabel, number, riskLabel } from '../format'
defineProps<{ report: ReportCandidate | FinalReport['content']; historical?: boolean }>()
defineEmits<{ citation: [claim: number, index: number] }>()
</script>
<template>
  <article class="report-candidate" :aria-label="historical ? '审核前提交版本' : report.status === 'approved' ? '已审核正式报告' : '未审核报告候选'">
    <div class="report-heading"><h3>{{ historical ? '审核前提交的报告候选' : report.title }}</h3><el-tag :type="report.status === 'approved' ? 'success' : 'warning'">{{ historical ? '审核前版本' : report.status === 'approved' ? '审核通过' : '未审核' }}</el-tag></div>
    <p v-if="historical" class="report-caption">这是审核前固定保存的提交版本，供追溯核对；当前处理结果以上方状态为准。</p>
    <p class="report-caption">固定流程：设备概况 → 趋势观察 → 可信预测{{ report.knowledge_status === 'not_requested' ? '' : ' → 资料核对' }}{{ report.status === 'approved' ? ' → 人工审核 → 正式报告' : ' → 报告候选' }}</p>
    <p>{{ engineLabel(report.trend.engine.engine_id) }} · 末端周期 {{ report.trend.end_cycle }} · 趋势区间 {{ report.trend.start_cycle }}–{{ report.trend.end_cycle }}</p>
    <div class="report-prediction"><strong>{{ number(report.prediction.business_rul) }}</strong> 运行周期<p>风险等级：{{ riskLabel[report.prediction.risk_level] }}。风险等级不是故障概率。</p></div>
    <h3>结论与依据</h3>
    <section v-for="section in report.sections" :key="section.id" class="report-section">
      <small>{{ section.kind === 'prediction' ? '可信预测' : section.kind === 'trend' ? '趋势观察' : '引用支持的资料结论' }}</small>
      <p>{{ section.text }}</p><p v-if="section.unit" class="report-caption">单位：{{ section.unit === '未知' ? '资料未注明单位' : section.unit }}</p>
      <el-button v-for="(ref, index) in section.citations" :key="index" link type="primary" @click="$emit('citation', section.claim_index!, index)">{{ ref.source_metadata.title }} · 版本 {{ ref.document_version }} · 查看原文</el-button>
    </section>
    <section><h3>无法确定与证据缺口</h3><p v-for="gap in report.gaps" :key="gap">{{ gap }}</p><p>现有预测与趋势不能确认故障部件、现场维修参数或失效日期。</p></section>
    <section><h3>{{ historical ? '提交时的核对事项' : report.status === 'approved' ? '审核核对事项' : '待人工核对' }}</h3><ul><li v-for="item in report.review_focus" :key="item.code">{{ item.text }}</li></ul></section>
    <section class="report-limits"><h3>使用限制</h3><p v-for="item in report.limitations" :key="item">{{ item }}</p></section>
    <details><summary>查看固定数据、预测身份与报告说明版本</summary><p>预测记录：{{ report.prediction.prediction_id }}</p><p>模型版本：{{ report.prediction.model_version }} · <router-link to="/model">模型说明</router-link></p><p>数据版本：{{ report.scope.data_version }}</p><p>报告说明：{{ report.skill.name }} · {{ report.skill.version }}</p><p>原始 RUL：{{ report.prediction.raw_rul }} · {{ report.prediction.postprocessing }}</p><el-table :data="report.trend.trends"><el-table-column prop="field" label="字段" min-width="115" /><el-table-column prop="first" label="起始值" min-width="110" /><el-table-column prop="last" label="末端值" min-width="110" /><el-table-column prop="change" label="变化" min-width="110" /><el-table-column prop="slope_per_cycle" label="每周期斜率" min-width="130" /></el-table></details>
  </article>
</template>
<style scoped>
.report-candidate{line-height:1.9}.report-heading{display:flex;justify-content:space-between;align-items:center;gap:12px}.report-heading h3{margin:0;font-size:18px}.report-caption,.report-section small{font-size:12px;color:#789078}.report-prediction{padding:20px;background:#edf5ec;border-radius:12px;margin:20px 0}.report-prediction strong{font-size:32px;color:#254d37}.report-candidate section{padding:12px 0;border-bottom:1px solid #e0e9dc}.report-section .el-button{white-space:normal;height:auto;text-align:left;line-height:1.8}.report-limits{color:#718467}.report-candidate h3{font-size:15px;margin:12px 0}.report-candidate ul{padding-left:22px}.report-candidate summary{cursor:pointer}.report-candidate details{margin-top:18px;overflow-wrap:anywhere;font-size:12px}
</style>
