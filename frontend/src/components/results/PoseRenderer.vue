<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'

import type { PoseResult } from '@/types/task'

const props = defineProps<{ imageUrl: string; result: PoseResult; confidence: number }>()
const canvas = ref<HTMLCanvasElement | null>(null)
const skeleton: Array<[string, string]> = [
  ['left_shoulder', 'right_shoulder'],
  ['left_shoulder', 'left_elbow'],
  ['left_elbow', 'left_wrist'],
  ['right_shoulder', 'right_elbow'],
  ['right_elbow', 'right_wrist'],
  ['left_shoulder', 'left_hip'],
  ['right_shoulder', 'right_hip'],
  ['left_hip', 'right_hip'],
  ['left_hip', 'left_knee'],
  ['left_knee', 'left_ankle'],
  ['right_hip', 'right_knee'],
  ['right_knee', 'right_ankle'],
]

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
  for (const instance of props.result.data.instances.filter((item) => item.score >= props.confidence)) {
    const points = new Map(
      instance.keypoints.filter((point) => point.score >= props.confidence).map((point) => [point.name, point]),
    )
    context.strokeStyle = '#a5ff69'
    context.lineWidth = 3
    for (const [from, to] of skeleton) {
      const start = points.get(from)
      const end = points.get(to)
      if (!start || !end) continue
      context.beginPath()
      context.moveTo(start.x, start.y)
      context.lineTo(end.x, end.y)
      context.stroke()
    }
    context.fillStyle = '#f0f5f1'
    for (const point of points.values()) {
      context.beginPath()
      context.arc(point.x, point.y, 5, 0, Math.PI * 2)
      context.fill()
    }
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
