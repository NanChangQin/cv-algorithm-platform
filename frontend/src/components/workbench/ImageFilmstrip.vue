<script setup lang="ts">
import { assetsApi } from '@/api/assets'
import UploadDropzone from '@/components/assets/UploadDropzone.vue'
import AppIcon from '@/components/layout/AppIcon.vue'
import type { ImageAsset } from '@/types/asset'
import type { TaskStatus } from '@/types/task'
import { taskStatusLabel } from '@/utils/status'

const props = withDefaults(defineProps<{
  items: ImageAsset[]
  total: number
  query: string
  selected: string[]
  focused: string | null
  loading?: boolean
  hasMore?: boolean
  uploading?: boolean
  progress?: number | null
  /** 本次批量推理里每张图的任务状态 */
  statuses?: Record<string, TaskStatus>
  max?: number
}>(), { loading: false, hasMore: false, uploading: false, progress: null, statuses: () => ({}), max: 50 })
const emit = defineEmits<{
  'update:query': [value: string]
  'update:selected': [value: string[]]
  'update:focused': [value: string]
  loadMore: []
  files: [files: File[]]
}>()

/**
 * 点未选中的图片：只选它（和相册一致）；点已选中的图片：只切换预览，不打乱多选。
 * 按住 Ctrl/⌘/Shift 点击，或点右上角的圆点，才是多选。
 */
function choose(asset: ImageAsset, event: MouseEvent) {
  if (event.ctrlKey || event.metaKey || event.shiftKey) { toggle(asset); return }
  if (!props.selected.includes(asset.id)) emit('update:selected', [asset.id])
  emit('update:focused', asset.id)
}
function toggle(asset: ImageAsset) {
  if (props.selected.includes(asset.id)) {
    if (props.selected.length > 1) emit('update:selected', props.selected.filter((id) => id !== asset.id))
    return
  }
  if (props.selected.length >= props.max) return
  emit('update:selected', [...props.selected, asset.id])
  if (!props.focused) emit('update:focused', asset.id)
}
function selectLoaded() {
  emit('update:selected', [...new Set([...props.selected, ...props.items.map((asset) => asset.id)])].slice(0, props.max))
}
function keepFocused() {
  const keep = props.focused && props.selected.includes(props.focused) ? props.focused : props.selected[0]
  if (keep) emit('update:selected', [keep])
}
</script>

<template>
  <section class="filmstrip" :class="{ multi: selected.length > 1 }" aria-label="输入图片">
    <header class="filmstrip-toolbar">
      <label class="search-field">
        <AppIcon name="search" />
        <input
          :value="query"
          type="search"
          placeholder="搜索图片文件名"
          aria-label="搜索图片文件名"
          @input="emit('update:query', ($event.target as HTMLInputElement).value)"
        />
      </label>
      <span class="muted-copy">
        共 {{ total }} 张<template v-if="selected.length > 1"> · 已选 {{ selected.length }} 张</template>
        <template v-if="selected.length >= max">（最多 {{ max }} 张）</template>
      </span>
      <span class="filmstrip-actions">
        <button v-if="selected.length > 1" type="button" class="text-button" @click="keepFocused">只保留当前图片</button>
        <button v-else-if="items.length > 1" type="button" class="text-button" @click="selectLoaded">全选已加载的图片</button>
      </span>
    </header>
    <div class="filmstrip-track">
      <UploadDropzone
        class="filmstrip-upload"
        tile
        accept-paste
        :disabled="uploading"
        :progress="progress"
        title="上传图片"
        hint="拖入、点击或粘贴"
        @files="emit('files', $event)"
      />
      <div
        v-for="asset in items"
        :key="asset.id"
        class="filmstrip-item"
        :class="{ selected: selected.includes(asset.id), focused: asset.id === focused }"
      >
        <button
          type="button"
          class="filmstrip-thumb"
          :aria-pressed="asset.id === focused"
          :title="`${asset.original_name} · ${asset.width} × ${asset.height}`"
          @click="choose(asset, $event)"
        >
          <img :src="assetsApi.thumbnailUrl(asset.id, 160)" :alt="asset.original_name" loading="lazy" />
          <span class="filmstrip-name">{{ asset.original_name }}</span>
        </button>
        <button
          type="button"
          class="filmstrip-check"
          :aria-label="selected.includes(asset.id) ? `取消多选 ${asset.original_name}` : `多选 ${asset.original_name}`"
          :aria-pressed="selected.includes(asset.id)"
          @click="toggle(asset)"
        >{{ selected.includes(asset.id) && selected.length > 1 ? selected.indexOf(asset.id) + 1 : '' }}</button>
        <i
          v-if="statuses[asset.id]"
          class="filmstrip-status"
          :class="`status-${statuses[asset.id]}`"
          :title="taskStatusLabel[statuses[asset.id]!]"
        />
      </div>
      <button v-if="hasMore" type="button" class="filmstrip-more" :disabled="loading" @click="emit('loadMore')">
        {{ loading ? '正在加载…' : `加载更多（还有 ${total - items.length} 张）` }}
      </button>
      <p v-if="!items.length && !loading" class="filmstrip-empty">
        {{ query ? `没有文件名包含“${query}”的图片` : '还没有图片：拖到左边的方框里，或直接 Ctrl+V 粘贴截图' }}
      </p>
    </div>
  </section>
</template>
