<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import type { ObjectDetectionResult } from '@/types/task'

const props = defineProps<{
  imageUrl: string
  result: ObjectDetectionResult
  confidence: number
  visibleLabels: string[]
  showAnnotations: boolean
}>()

const canvas = ref<HTMLCanvasElement | null>(null)
const stage = ref<HTMLDivElement | null>(null)
const scale = ref(1)
const autoFit = ref(true)
const loadError = ref('')
let image: HTMLImageElement | null = null
let resizeObserver: ResizeObserver | null = null
let renderVersion = 0
let observedWidth = 0

const visibleDetections = computed(() => props.result.data.detections.filter(
  (item) => item.score >= props.confidence && props.visibleLabels.includes(item.label),
))

function fitToStage() {
  autoFit.value = true
  if (stage.value) {
    scale.value = Math.min(1, Math.max(0.1, (stage.value.clientWidth - 32) / props.result.input.width))
  }
}

async function render() {
  const version = ++renderVersion
  if (!canvas.value || !props.imageUrl) return
  try {
    if (!image || image.src !== new URL(props.imageUrl, window.location.href).href) {
      const next = new Image()
      next.src = props.imageUrl
      await next.decode()
      if (version !== renderVersion || !canvas.value) return
      image = next
    }
    loadError.value = ''
  } catch {
    if (version === renderVersion) loadError.value = '图片加载失败，请刷新页面重试。'
    return
  }
  const width = Math.max(1, Math.round(props.result.input.width * scale.value))
  const height = Math.max(1, Math.round(props.result.input.height * scale.value))
  canvas.value.width = width
  canvas.value.height = height
  const context = canvas.value.getContext('2d')
  if (!context || !image) return
  context.drawImage(image, 0, 0, width, height)
  if (!props.showAnnotations) return
  context.lineWidth = Math.max(2, 3 * scale.value)
  context.font = `${Math.max(12, 14 * scale.value)}px Manrope, sans-serif`
  for (const detection of visibleDetections.value) {
    const [x1, y1, x2, y2] = detection.bbox.map((value) => value * scale.value) as [number, number, number, number]
    const label = `${detection.label} ${(detection.score * 100).toFixed(1)}%`
    const metrics = context.measureText(label)
    context.strokeStyle = '#a5ff69'
    context.fillStyle = '#a5ff69'
    context.strokeRect(x1, y1, x2 - x1, y2 - y1)
    context.fillRect(x1, Math.max(0, y1 - 22), metrics.width + 10, 22)
    context.fillStyle = '#0b100e'
    context.fillText(label, x1 + 5, Math.max(15, y1 - 6))
  }
}

function downloadRendered() {
  const link = document.createElement('a')
  link.download = `result-${props.result.request_id}.png`
  link.href = canvas.value?.toDataURL('image/png') ?? ''
  link.click()
}

watch(
  () => [props.imageUrl, props.result, props.confidence, props.visibleLabels, props.showAnnotations, scale.value],
  () => { void render() },
  { deep: true },
)

onMounted(() => {
  fitToStage()
  void render()
  if (typeof ResizeObserver !== 'undefined' && stage.value) {
    resizeObserver = new ResizeObserver(([entry]) => {
      if (!entry || entry.contentRect.width === observedWidth) return
      observedWidth = entry.contentRect.width
      if (autoFit.value) fitToStage()
    })
    resizeObserver.observe(stage.value)
  }
})

onBeforeUnmount(() => {
  renderVersion++
  resizeObserver?.disconnect()
  image = null
})

defineExpose({ downloadRendered })
</script>

<template>
  <div class="canvas-shell">
    <div class="canvas-toolbar">
      <label>缩放
        <input v-model.number="scale" type="range" min="0.1" max="2" step="0.05" aria-label="结果图片缩放" @input="autoFit = false" />
        <output>{{ Math.round(scale * 100) }}%</output>
      </label>
      <div class="toolbar-actions">
        <button class="secondary-button" @click="fitToStage">适应窗口</button>
        <button class="secondary-button" :disabled="Boolean(loadError)" @click="downloadRendered">下载结果图</button>
      </div>
    </div>
    <p v-if="loadError" class="alert" role="alert">{{ loadError }}</p>
    <div ref="stage" class="canvas-stage">
      <canvas ref="canvas" aria-label="算法检测结果图" />
    </div>
  </div>
</template>
