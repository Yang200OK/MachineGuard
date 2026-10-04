<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent, DataZoomComponent, AriaComponent } from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'
import type { EChartsOption } from 'echarts'

echarts.use([LineChart, GridComponent, TooltipComponent, LegendComponent, DataZoomComponent, AriaComponent, SVGRenderer])
const props = defineProps<{ options: EChartsOption; label: string }>()
const container = ref<HTMLDivElement>()
let chart: ReturnType<typeof echarts.init> | undefined
let observer: ResizeObserver | undefined
onMounted(() => {
  if (!container.value) throw new Error('图表容器未挂载。')
  chart = echarts.init(container.value, undefined, { renderer: 'svg' })
  chart.setOption(props.options)
  // 面板和屏幕尺寸变化时重绘，离开页面释放实例，避免重复监听。
  observer = new ResizeObserver(() => chart?.resize())
  observer.observe(container.value)
})
watch(() => props.options, options => chart?.setOption(options, { notMerge: true }))
onBeforeUnmount(() => { observer?.disconnect(); chart?.dispose() })
</script>
<template><div ref="container" class="line-chart" role="img" :aria-label="label" /></template>
