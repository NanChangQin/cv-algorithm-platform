<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'

import type { OcrResult } from '@/types/task'

const props = defineProps<{ imageUrl: string; result: OcrResult; confidence: number }>()
const canvas = ref<HTMLCanvasElement | null>(null)

async function render() {
  if (!canvas.value) return
  const image = new Image()
  image.src = props.imageUrl
  await image.decode()
  canvas.value.width = props.result.input.width
  canvas.value.height = props.result.input.height
  const context = canvas.value.getContext('2d')
  if (!context) return
  context.drawImage(image, 0, 0, canvas.value.width, canvas.value.height)
  context.lineWidth = 3
  context.font = '16px Manrope, sans-serif'
  for (const text of props.result.data.texts.filter((item) => item.score >= props.confidence)) {
    if (text.polygon.length < 3) continue
    const [first, ...rest] = text.polygon as [number[], ...number[][]]
    context.beginPath()
    context.moveTo(first[0]!, first[1]!)
    for (const point of rest) context.lineTo(point[0]!, point[1]!)
    context.closePath()
    context.strokeStyle = '#a5ff69'
    context.stroke()
    const left = Math.min(...text.polygon.map((point) => point[0]!))
    const top = Math.min(...text.polygon.map((point) => point[1]!))
    context.fillStyle = '#a5ff69'
    context.fillText(`${text.text} ${(text.score * 100).toFixed(1)}%`, left, Math.max(16, top - 5))
  }
}

watch(() => [props.imageUrl, props.confidence, props.result], () => { void render() }, { deep: true })
onMounted(() => { void render() })
</script>

<template>
  <div class="canvas-shell">
    <div class="canvas-stage">
      <canvas ref="canvas" />
    </div>
  </div>
</template>
