<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const props = withDefaults(defineProps<{
  accept?: string
  multiple?: boolean
  disabled?: boolean
  /** 上传进度 0–1；为 null 时不显示进度条 */
  progress?: number | null
  /** 是否接收页面上的 Ctrl+V 粘贴（截图、复制的图片） */
  acceptPaste?: boolean
  title?: string
  hint?: string
  compact?: boolean
  /** 方块样式，放在图片列表里当第一格 */
  tile?: boolean
}>(), {
  accept: 'image/jpeg,image/png,image/bmp,image/webp',
  multiple: true,
  disabled: false,
  progress: null,
  acceptPaste: false,
  title: '拖入图片，或点击选择',
  hint: '支持 JPG、PNG、BMP、WebP，可多选',
  compact: false,
  tile: false,
})
const emit = defineEmits<{ files: [files: File[]] }>()
const input = ref<HTMLInputElement | null>(null)
const dragging = ref(false)
let dragDepth = 0
const accepted = computed(() => props.accept.split(',').map((item) => item.trim().toLowerCase()).filter(Boolean))

function matches(file: File) {
  const name = file.name.toLowerCase()
  return accepted.value.some((rule) => rule.startsWith('.') ? name.endsWith(rule) : rule.endsWith('/*') ? file.type.startsWith(rule.slice(0, -1)) : file.type === rule)
}
function deliver(list: Iterable<File>) {
  if (props.disabled) return
  const files = [...list].filter(matches)
  if (files.length) emit('files', props.multiple ? files : files.slice(0, 1))
}
function onChange(event: Event) {
  const target = event.target as HTMLInputElement
  deliver(target.files ?? [])
  target.value = ''
}
function onDragEnter() { dragDepth++; dragging.value = !props.disabled }
function onDragLeave() { dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) dragging.value = false }
function onDrop(event: DragEvent) {
  dragDepth = 0
  dragging.value = false
  deliver(event.dataTransfer?.files ?? [])
}
function onPaste(event: ClipboardEvent) {
  const target = event.target as HTMLElement | null
  if (target?.closest('input, textarea, [contenteditable="true"]')) return
  const files = [...(event.clipboardData?.files ?? [])]
  if (!files.length) return
  event.preventDefault()
  // 剪贴板里的截图通常叫 image.png，补上时间避免重名
  const stamp = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14)
  deliver(files.map((file, index) => file.name === 'image.png'
    ? new File([file], `粘贴-${stamp}${files.length > 1 ? `-${index + 1}` : ''}.png`, { type: file.type })
    : file))
}
onMounted(() => { if (props.acceptPaste) window.addEventListener('paste', onPaste) })
onBeforeUnmount(() => window.removeEventListener('paste', onPaste))
defineExpose({ open: () => input.value?.click() })
</script>

<template>
  <label
    class="dropzone"
    :class="{ dragging, disabled, compact, tile }"
    @dragenter.prevent="onDragEnter"
    @dragover.prevent
    @dragleave.prevent="onDragLeave"
    @drop.prevent="onDrop"
  >
    <span class="dropzone-icon" aria-hidden="true">↑</span>
    <span class="dropzone-copy">
      <strong>{{ progress !== null ? `正在上传 ${Math.round(progress * 100)}%` : dragging ? '松开即可上传' : title }}</strong>
      <small>{{ hint }}<template v-if="acceptPaste && !tile">；也可以直接 Ctrl+V 粘贴截图</template></small>
    </span>
    <span v-if="progress !== null" class="dropzone-progress" role="progressbar" :aria-valuenow="Math.round(progress * 100)" aria-valuemin="0" aria-valuemax="100"><i :style="{ width: `${progress * 100}%` }" /></span>
    <input ref="input" type="file" :accept="accept" :multiple="multiple" :disabled="disabled" @change="onChange" />
  </label>
</template>
