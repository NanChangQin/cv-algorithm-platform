<script setup lang="ts">
import { computed, ref } from 'vue'
import { assetsApi } from '@/api/assets'
import type { DetectionBox } from '@/api/evaluations'
const props = defineProps<{ assetId: string; width: number; height: number; suggestions: string[] }>()
const boxes = defineModel<DetectionBox[]>({ required: true })
const selected = ref(-1)
const mode = ref<'select' | 'draw'>('select')
const label = ref('')
const svg = ref<SVGSVGElement | null>(null)
let drag: { x: number; y: number; index: number; original: number[]; kind: 'move' | 'resize' | 'draw' } | null = null
const active = computed(() => boxes.value[selected.value])
function point(event: PointerEvent) {
  const matrix = svg.value?.getScreenCTM()?.inverse()
  const p = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix)
  return { x: Math.max(0, Math.min(props.width, p.x)), y: Math.max(0, Math.min(props.height, p.y)) }
}
function begin(event: PointerEvent, index = -1, resize = false) {
  if (event.button !== 0) return
  const p = point(event)
  let original: number[]
  if (mode.value === 'draw') {
    if (!label.value.trim()) return
    const box: DetectionBox = { label: label.value.trim(), bbox: [p.x, p.y, p.x, p.y] }
    index = boxes.value.length
    original = [...box.bbox]
    boxes.value = [...boxes.value, box]
  } else {
    if (index < 0) { selected.value = -1; return }
    original = [...boxes.value[index]!.bbox]
  }
  selected.value = index
  drag = { ...p, index, original, kind: mode.value === 'draw' ? 'draw' : resize ? 'resize' : 'move' }
  svg.value?.setPointerCapture(event.pointerId)
}
function move(event: PointerEvent) {
  if (!drag) return
  const p = point(event), [x1,y1,x2,y2] = drag.original as [number,number,number,number]
  let value: [number,number,number,number]
  if (drag.kind === 'draw') value = [Math.min(drag.x,p.x),Math.min(drag.y,p.y),Math.max(drag.x,p.x),Math.max(drag.y,p.y)]
  else if (drag.kind === 'resize') value = [x1,y1,Math.max(x1+1,p.x),Math.max(y1+1,p.y)]
  else {
    const dx = Math.max(-x1,Math.min(props.width-x2,p.x-drag.x)), dy = Math.max(-y1,Math.min(props.height-y2,p.y-drag.y))
    value = [x1+dx,y1+dy,x2+dx,y2+dy]
  }
  boxes.value = boxes.value.map((b,i) => i === drag!.index ? {...b,bbox:value} : b)
}
function end() {
  if (drag) {
    const box = boxes.value[drag.index]
    if (box && (box.bbox[2]-box.bbox[0]<1 || box.bbox[3]-box.bbox[1]<1)) boxes.value = boxes.value.filter((_,i)=>i!==drag!.index)
    drag = null
  }
}
function remove() { boxes.value = boxes.value.filter((_,i)=>i!==selected.value); selected.value=-1 }
function changeLabel(event: Event) { if(active.value) boxes.value = boxes.value.map((b,i)=>i===selected.value?{...b,label:(event.target as HTMLInputElement).value}:b) }
function coordinate(index: number, event: Event) { if(active.value) { const bbox=[...active.value.bbox] as DetectionBox['bbox'];bbox[index]=Number((event.target as HTMLInputElement).value); boxes.value=boxes.value.map((b,i)=>i===selected.value?{...b,bbox}:b) } }
</script>
<template>
  <div class="detection-editor">
    <div class="toolbar-actions"><button class="secondary-button" :aria-pressed="mode === 'select'" @click="mode='select'">选择 / 移动框</button><label>新增框类别<input v-model="label" aria-label="新增框类别" list="review-labels" maxlength="64" /></label><button class="secondary-button" :aria-pressed="mode === 'draw'" :disabled="!label.trim()" @click="mode='draw'">绘制新框</button></div>
    <datalist id="review-labels"><option v-for="value in suggestions" :key="value" :value="value" /></datalist>
    <p class="muted-copy">选择框后可拖动位置、拖动右下角调整大小，或编辑精确坐标。绘制模式下在图片上拖动新增；所有框均需人工审核。</p>
    <svg ref="svg" :viewBox="`0 0 ${width} ${height}`" role="img" aria-label="检测框编辑画布" :class="{drawing:mode==='draw'}" @pointerdown="begin($event)" @pointermove="move" @pointerup="end" @pointercancel="end">
      <image :href="assetsApi.contentUrl(assetId)" :width="width" :height="height" />
      <g v-for="(box,index) in boxes" :key="index" @pointerdown.stop="begin($event,index)">
        <rect :x="box.bbox[0]" :y="box.bbox[1]" :width="Math.max(0,box.bbox[2]-box.bbox[0])" :height="Math.max(0,box.bbox[3]-box.bbox[1])" :stroke="selected===index?'#f59e0b':'#22d3ee'" fill="transparent" stroke-width="2" vector-effect="non-scaling-stroke" />
        <text :x="box.bbox[0]" :y="Math.max(16,box.bbox[1]-5)" :font-size="Math.max(width/70,14)" fill="white" stroke="black" stroke-width=".5">{{ index+1 }} · {{ box.label }}</text>
        <rect v-if="selected===index" :x="box.bbox[2]-width/140" :y="box.bbox[3]-width/140" :width="width/70" :height="width/70" fill="#f59e0b" @pointerdown.stop="begin($event,index,true)" />
      </g>
    </svg>
    <div class="review-box-tools"><label>选择标注框<select v-model.number="selected" aria-label="选择标注框"><option :value="-1">未选择 · 共 {{ boxes.length }} 个框</option><option v-for="(box,index) in boxes" :key="index" :value="index">{{ index+1 }} · {{ box.label }}</option></select></label><template v-if="active"><label>类别<input :value="active.label" list="review-labels" aria-label="修改框类别" @input="changeLabel" /></label><label v-for="(name,index) in ['左 x','上 y','右 x','下 y']" :key="name">{{ name }}<input :value="active.bbox[index]" type="number" step="1" :aria-label="name" @input="coordinate(index,$event)" /></label><button class="secondary-button danger-button" @click="remove">删除选中框</button></template></div>
    <p v-if="!boxes.length" class="build-notice">当前没有标注框；审核通过后将作为背景图片。请确认图中确实没有需要标注的目标。</p>
  </div>
</template>
<style scoped>
.toolbar-actions{align-items:flex-end}
svg { width:100%; max-height:65vh; background:#162034; touch-action:none; margin:16px 0; }
svg.drawing { cursor:crosshair; }
.review-box-tools { display:flex; flex-wrap:wrap; gap:12px; align-items:end; }
label { display:flex; flex-direction:column; gap:6px; font-size:13px; }
input,select { padding:8px; border:1px solid var(--line-strong);border-radius:7px;background:var(--surface);color:var(--text);max-width:100%; }
input[type=number] { width:90px; }
</style>
