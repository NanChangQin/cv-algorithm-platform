<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

export interface ChartSeries {
  label: string
  /** 1–4，对应 --series-1 … --series-4 */
  slot: number
  points: Array<[number, number]>
}

const props = withDefaults(defineProps<{
  title: string
  series: ChartSeries[]
  /** 横轴总轮数：训练进行中也按全部轮数画横轴 */
  epochs: number
  /** 纵轴上限；不传时按数据自动取整 */
  yMax?: number
  format?: (value: number) => string
  height?: number
}>(), { yMax: undefined, format: (value: number) => value.toFixed(3), height: 180 })

const root = ref<HTMLElement | null>(null)
const width = ref(480)
const hoverEpoch = ref<number | null>(null)
const padding = { left: 44, right: 14, top: 12, bottom: 24 }
let observer: ResizeObserver | null = null

onMounted(() => {
  if (!root.value) return
  width.value = root.value.clientWidth || width.value
  if (typeof ResizeObserver === 'undefined') return
  observer = new ResizeObserver(([entry]) => { if (entry) width.value = Math.max(240, entry.contentRect.width) })
  observer.observe(root.value)
})
onBeforeUnmount(() => observer?.disconnect())

const plotWidth = computed(() => width.value - padding.left - padding.right)
const plotHeight = computed(() => props.height - padding.top - padding.bottom)
const xMax = computed(() => Math.max(2, props.epochs, ...props.series.flatMap((item) => item.points.map(([x]) => x))))
const yTop = computed(() => {
  if (props.yMax !== undefined) return props.yMax
  const max = Math.max(0, ...props.series.flatMap((item) => item.points.map(([, y]) => y)))
  if (max <= 0) return 1
  const magnitude = 10 ** Math.floor(Math.log10(max))
  return Math.ceil(max / magnitude * 2) / 2 * magnitude
})
const x = (value: number) => padding.left + ((value - 1) / (xMax.value - 1)) * plotWidth.value
const y = (value: number) => padding.top + (1 - value / yTop.value) * plotHeight.value

const yTicks = computed(() => [0, 0.25, 0.5, 0.75, 1].map((ratio) => ratio * yTop.value))
const xTicks = computed(() => {
  const step = Math.max(1, Math.ceil((xMax.value - 1) / 5))
  const ticks = []
  for (let value = 1; value <= xMax.value; value += step) ticks.push(value)
  if (ticks[ticks.length - 1] !== xMax.value) ticks.push(xMax.value)
  return ticks
})
const paths = computed(() => props.series.map((item) => ({
  ...item,
  d: item.points.map(([px, py], index) => `${index ? 'L' : 'M'}${x(px).toFixed(1)},${y(py).toFixed(1)}`).join(''),
  last: item.points[item.points.length - 1],
})))
const hasData = computed(() => props.series.some((item) => item.points.length))
const hoverValues = computed(() => {
  if (hoverEpoch.value === null) return []
  return props.series.flatMap((item) => {
    const point = item.points.find(([px]) => px === hoverEpoch.value)
    return point ? [{ label: item.label, slot: item.slot, value: point[1] }] : []
  })
})
const tooltipLeft = computed(() => {
  if (hoverEpoch.value === null) return 0
  const position = x(hoverEpoch.value)
  return position > width.value - 170 ? position - 160 : position + 12
})

function onPointer(event: PointerEvent) {
  const box = (event.currentTarget as SVGRectElement).getBoundingClientRect()
  const ratio = (event.clientX - box.left) / box.width
  const epoch = Math.round(1 + ratio * (xMax.value - 1))
  const known = props.series.some((item) => item.points.some(([px]) => px === epoch))
  hoverEpoch.value = known ? epoch : null
}
</script>

<template>
  <figure ref="root" class="training-chart">
    <figcaption>
      <strong>{{ title }}</strong>
      <span v-if="series.length > 1" class="chart-legend">
        <span v-for="item in series" :key="item.label"><i :style="{ background: `var(--series-${item.slot})` }" />{{ item.label }}</span>
      </span>
    </figcaption>
    <div class="chart-canvas">
      <svg :width="width" :height="height" role="img" :aria-label="`${title}，横轴为训练轮次`">
        <g class="chart-grid">
          <line v-for="tick in yTicks" :key="tick" :x1="padding.left" :x2="width - padding.right" :y1="y(tick)" :y2="y(tick)" />
        </g>
        <g class="chart-axis">
          <text v-for="tick in yTicks" :key="`y${tick}`" :x="padding.left - 6" :y="y(tick) + 4" text-anchor="end">{{ format(tick) }}</text>
          <text v-for="tick in xTicks" :key="`x${tick}`" :x="x(tick)" :y="height - 6" text-anchor="middle">{{ tick }}</text>
        </g>
        <template v-for="item in paths" :key="item.label">
          <path v-if="item.points.length > 1" :d="item.d" class="chart-line" :style="{ stroke: `var(--series-${item.slot})` }" />
          <circle v-else-if="item.last" :cx="x(item.last[0])" :cy="y(item.last[1])" r="4" class="chart-dot" :style="{ fill: `var(--series-${item.slot})` }" />
        </template>
        <g v-if="hoverEpoch !== null">
          <line class="chart-crosshair" :x1="x(hoverEpoch)" :x2="x(hoverEpoch)" :y1="padding.top" :y2="padding.top + plotHeight" />
          <circle v-for="item in hoverValues" :key="item.label" :cx="x(hoverEpoch)" :cy="y(item.value)" r="4" class="chart-dot" :style="{ fill: `var(--series-${item.slot})` }" />
        </g>
        <rect
          :x="padding.left" :y="padding.top" :width="Math.max(0, plotWidth)" :height="Math.max(0, plotHeight)"
          fill="transparent" @pointermove="onPointer" @pointerleave="hoverEpoch = null"
        />
      </svg>
      <p v-if="!hasData" class="chart-empty">第一轮结束后显示曲线</p>
      <div v-if="hoverEpoch !== null && hoverValues.length" class="chart-tooltip" :style="{ left: `${tooltipLeft}px` }">
        <strong>第 {{ hoverEpoch }} 轮</strong>
        <span v-for="item in hoverValues" :key="item.label"><i :style="{ background: `var(--series-${item.slot})` }" />{{ item.label }}<b>{{ format(item.value) }}</b></span>
      </div>
    </div>
  </figure>
</template>
