<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { GraphChart } from 'echarts/charts'
import { TooltipComponent, AriaComponent } from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'
import type { GraphEntity, GraphRelation } from '../graphTypes'

echarts.use([GraphChart, TooltipComponent, AriaComponent, SVGRenderer])
const props = defineProps<{ relations: GraphRelation[] }>()
const emit = defineEmits<{ inspect: [id: string]; paths: [entity: GraphEntity] }>()
const entities = computed(() => new Map(props.relations.flatMap(row => [row.subject, row.object]).map(node => [node.entity_id, node])))
const container = ref<HTMLDivElement>()
let chart: ReturnType<typeof echarts.init> | undefined
let observer: ResizeObserver | undefined
const options = computed(() => {
  const nodes = new Map(props.relations.flatMap(row => [row.subject, row.object]).map(node => [node.entity_id, node]))
  return { aria: { enabled: true }, tooltip: { renderMode: 'richText' as const,
    formatter: (params: { data: unknown; dataType?: string; name: string }) => params.dataType === 'edge' ? (params.data as { predicate: string }).predicate : params.name }, series: [{ type: 'graph' as const, layout: 'circular' as const,
    roam: true, symbolSize: 42, edgeSymbol: ['none', 'arrow'], edgeSymbolSize: 8,
    data: [...nodes.values()].map(node => ({ id: node.entity_id, name: `${node.name}\n${node.document_title}`, label: { formatter: node.name.length > 14 ? node.name.slice(0, 14) + '…' : node.name } })),
    links: props.relations.map(row => ({ source: row.subject.entity_id, target: row.object.entity_id, predicate: row.predicate, relationId: row.relation_id })),
    label: { show: true, color: '#284e41', fontSize: 12 }, edgeLabel: { show: true, formatter: (params: { data: unknown }) => (params.data as { predicate: string }).predicate, fontSize: 11 },
    lineStyle: { color: '#668e78', curveness: 0.12, width: 2 }, itemStyle: { color: '#c8dfce', borderColor: '#547d66', borderWidth: 1 } }] }
})
onMounted(() => {
  if (!container.value) throw new Error('图谱容器未挂载。')
  chart = echarts.init(container.value, undefined, { renderer: 'svg' }); chart.setOption(options.value)
  chart.on('click', params => {
    const data = params.data as { relationId?: string; id?: string }
    if (data.relationId) emit('inspect', data.relationId)
    else if (data.id && entities.value.has(data.id)) emit('paths', entities.value.get(data.id)!)
  })
  observer = new ResizeObserver(() => chart?.resize()); observer.observe(container.value)
})
watch(options, value => chart?.setOption(value, { notMerge: true }))
onBeforeUnmount(() => { observer?.disconnect(); chart?.dispose() })
</script>
<template><div ref="container" class="source-graph" role="img" aria-label="有效关系图：点击实体查询两跳内路径，点击连线查看原文" /><details class="graph-keyboard"><summary>按名称选择实体</summary><el-button v-for="entity in entities.values()" :key="entity.entity_id" text @click="$emit('paths', entity)">{{ entity.name }} · {{ entity.document_title }}</el-button></details></template>
<style scoped>.graph-keyboard{font-size:12px;margin:12px 0;color:#648070}.graph-keyboard summary{cursor:pointer}.graph-keyboard .el-button{white-space:normal;height:auto;line-height:1.7;margin:8px 6px 0 0}.source-graph{height:420px;width:100%;min-width:0}@media(max-width:760px){.source-graph{height:340px}}</style>
