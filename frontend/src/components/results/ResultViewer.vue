<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'

import ClassificationRenderer from './ClassificationRenderer.vue'
import DetectionCanvas from './DetectionCanvas.vue'
import OcrRenderer from './OcrRenderer.vue'
import PoseRenderer from './PoseRenderer.vue'
import SegmentationRenderer from './SegmentationRenderer.vue'
import type { AlgorithmResult, Detection } from '@/types/task'
import { formatMs } from '@/utils/presentation'

const props = withDefaults(defineProps<{
  result: AlgorithmResult
  imageUrl: string
  /** 工作台、抽屉：图片按窗口高度缩放，整张留在一屏内 */
  compact?: boolean
  /** compact 时画布上方和下方其他内容大约占的高度（像素） */
  fitOffset?: number
}>(), { compact: false, fitOffset: 340 })

const confidence = ref(0)
const viewportHeight = ref(window.innerHeight)
/** 工作台、抽屉里让整张图留在一屏内 */
const canvasMaxHeight = computed(() => props.compact ? Math.max(280, viewportHeight.value - props.fitOffset) : undefined)
function onResize() { viewportHeight.value = window.innerHeight }
onMounted(() => window.addEventListener('resize', onResize))
onUnmounted(() => window.removeEventListener('resize', onResize))
const showAnnotations = ref(true)
const hiddenLabels = ref<string[]>([])
const highlighted = ref<Detection | null>(null)
const showAllDetections = ref(false)

// 换了一个结果就恢复默认视图，避免沿用上一张图的筛选
watch(() => props.result, () => {
  confidence.value = 0
  hiddenLabels.value = []
  highlighted.value = null
  showAllDetections.value = false
})

const detections = computed(() => props.result.type === 'object_detection' ? props.result.data.detections : [])
const labels = computed(() => [...new Set(detections.value.map((item) => item.label))].sort())
const labelCounts = computed(() => {
  const counts = new Map<string, number>()
  for (const item of detections.value) {
    if (item.score >= confidence.value) counts.set(item.label, (counts.get(item.label) ?? 0) + 1)
  }
  return counts
})
const visibleLabels = computed(() => labels.value.filter((label) => !hiddenLabels.value.includes(label)))
const visibleDetections = computed(() => detections.value
  .filter((item) => item.score >= confidence.value && !hiddenLabels.value.includes(item.label))
  .sort((a, b) => b.score - a.score))
const listedDetections = computed(() => showAllDetections.value ? visibleDetections.value : visibleDetections.value.slice(0, 20))
const summary = computed(() => {
  const value = props.result
  if (value.type === 'object_detection') return `${visibleDetections.value.length} 个目标`
  if (value.type === 'classification') return `${value.data.predictions.length} 个分类`
  if (value.type === 'segmentation') return `${value.data.segments.length} 个分割区域`
  if (value.type === 'ocr') return `${value.data.texts.length} 段文本`
  return `${value.data.instances.length} 个姿态`
})

function toggleLabel(label: string) {
  hiddenLabels.value = hiddenLabels.value.includes(label)
    ? hiddenLabels.value.filter((item) => item !== label)
    : [...hiddenLabels.value, label]
}
function boxSize(detection: Detection) {
  const [x1, y1, x2, y2] = detection.bbox
  return `${Math.round(x2 - x1)} × ${Math.round(y2 - y1)}`
}
</script>

<template>
  <section class="result-viewer" :class="{ compact }" :style="canvasMaxHeight ? { '--result-max-height': `${canvasMaxHeight}px` } : undefined">
    <div class="result-toolbar">
      <label class="confidence-control">
        <span>最低置信度</span>
        <input v-model.number="confidence" aria-label="结果最低置信度" type="range" min="0" max="1" step="0.01" />
        <output>{{ confidence.toFixed(2) }}</output>
      </label>
      <label v-if="result.type === 'object_detection'" class="check-control">
        <input v-model="showAnnotations" type="checkbox" role="switch" /> 显示结果图层
      </label>
      <span class="count-badge">{{ summary }}</span>
    </div>
    <div v-if="labels.length" class="filter-chips">
      <span>显示类别</span>
      <button
        v-for="label in labels"
        :key="label"
        type="button"
        :class="{ disabled: hiddenLabels.includes(label) }"
        :aria-pressed="!hiddenLabels.includes(label)"
        @click="toggleLabel(label)"
      >{{ label }}<small>{{ labelCounts.get(label) ?? 0 }}</small></button>
      <button v-if="hiddenLabels.length" type="button" class="text-button" @click="hiddenLabels = []">恢复全部</button>
    </div>
    <div v-if="result.type === 'object_detection'" class="detection-layout">
      <DetectionCanvas
        :image-url="imageUrl"
        :result="result"
        :confidence="confidence"
        :visible-labels="visibleLabels"
        :show-annotations="showAnnotations"
        :highlight="highlighted"
        :max-height="canvasMaxHeight"
        @hover="highlighted = $event"
      />
      <aside class="detection-list" aria-label="检测结果列表">
        <header><strong>检测结果</strong><span>{{ visibleDetections.length }} 个</span></header>
        <p v-if="!visibleDetections.length" class="muted-copy">没有达到当前置信度的目标，可以调低“最低置信度”。</p>
        <table v-else>
          <thead><tr><th>类别</th><th>置信度</th><th>尺寸</th></tr></thead>
          <tbody>
            <tr
              v-for="(detection, index) in listedDetections"
              :key="index"
              :class="{ active: highlighted === detection }"
              tabindex="0"
              @mouseenter="highlighted = detection"
              @mouseleave="highlighted = null"
              @focus="highlighted = detection"
              @blur="highlighted = null"
            >
              <td>{{ detection.label }}</td>
              <td><span class="score-bar"><i :style="{ width: `${detection.score * 100}%` }" /></span>{{ (detection.score * 100).toFixed(1) }}%</td>
              <td class="mono">{{ boxSize(detection) }}</td>
            </tr>
          </tbody>
        </table>
        <button v-if="visibleDetections.length > listedDetections.length" type="button" class="text-button" @click="showAllDetections = true">
          显示全部 {{ visibleDetections.length }} 个
        </button>
      </aside>
    </div>
    <ClassificationRenderer v-else-if="result.type === 'classification'" :result="result" :confidence="confidence" />
    <SegmentationRenderer v-else-if="result.type === 'segmentation'" :image-url="imageUrl" :result="result" :confidence="confidence" />
    <OcrRenderer v-else-if="result.type === 'ocr'" :image-url="imageUrl" :result="result" :confidence="confidence" />
    <PoseRenderer v-else-if="result.type === 'pose_estimation'" :image-url="imageUrl" :result="result" :confidence="confidence" />
    <dl class="timing-row">
      <div><dt>预处理</dt><dd>{{ formatMs(result.timing?.preprocess_ms) }}</dd></div>
      <div><dt>模型推理</dt><dd>{{ formatMs(result.timing?.inference_ms) }}</dd></div>
      <div><dt>后处理</dt><dd>{{ formatMs(result.timing?.postprocess_ms) }}</dd></div>
      <div><dt>输入尺寸</dt><dd>{{ result.input.width }} × {{ result.input.height }}</dd></div>
    </dl>
  </section>
</template>
