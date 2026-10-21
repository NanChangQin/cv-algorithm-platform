<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { assetsApi } from '@/api/assets'
import type { ImageAsset } from '@/types/asset'

const props = withDefaults(defineProps<{
  assets: ImageAsset[]
  modelValue: string[]
  multiple?: boolean
  max?: number
  pageSize?: number
}>(), { multiple: false, max: 50, pageSize: 24 })
const emit = defineEmits<{ 'update:modelValue': [value: string[]] }>()

const query = ref('')
const visibleCount = ref(props.pageSize)
const filtered = computed(() => {
  const keyword = query.value.trim().toLowerCase()
  return keyword ? props.assets.filter((asset) => asset.original_name.toLowerCase().includes(keyword)) : props.assets
})
const shown = computed(() => filtered.value.slice(0, visibleCount.value))
watch(query, () => { visibleCount.value = props.pageSize })

/** 点击图片只选这一张（和相册一致）；按住 Ctrl/⌘/Shift 点击，或勾选右上角方框才是多选 */
function choose(asset: ImageAsset, event: MouseEvent) {
  if (props.multiple && (event.ctrlKey || event.metaKey || event.shiftKey)) toggle(asset)
  else emit('update:modelValue', [asset.id])
}
function toggle(asset: ImageAsset) {
  const selected = props.modelValue
  if (selected.includes(asset.id)) emit('update:modelValue', selected.filter((id) => id !== asset.id))
  else if (selected.length < props.max) emit('update:modelValue', [...selected, asset.id])
}
function selectFiltered() {
  const ids = [...new Set([...props.modelValue, ...filtered.value.map((asset) => asset.id)])].slice(0, props.max)
  emit('update:modelValue', ids)
}
</script>

<template>
  <div class="asset-picker">
    <div v-if="assets.length > 6 || multiple" class="asset-picker-tools">
      <input v-if="assets.length > 6" v-model="query" type="search" placeholder="按文件名搜索图片" aria-label="按文件名搜索图片" />
      <template v-if="multiple">
        <span class="muted-copy">已选 {{ modelValue.length }}<template v-if="modelValue.length >= max">（最多 {{ max }} 张）</template></span>
        <button type="button" class="text-button" :disabled="!filtered.length" @click="selectFiltered">{{ query ? '选择搜索结果' : '全选' }}</button>
        <button v-if="modelValue.length > 1" type="button" class="text-button" @click="emit('update:modelValue', modelValue.slice(0, 1))">只保留第一张</button>
      </template>
    </div>
    <p v-if="!filtered.length" class="muted-copy">没有文件名包含“{{ query }}”的图片。</p>
    <div class="asset-picker-grid" aria-label="选择输入图片">
      <div v-for="asset in shown" :key="asset.id" class="asset-option" :class="{ selected: modelValue.includes(asset.id) }">
        <button
          type="button"
          class="asset-option-main"
          :aria-pressed="modelValue.includes(asset.id)"
          :title="`${asset.original_name} · ${asset.width} × ${asset.height}`"
          @click="choose(asset, $event)"
        >
          <img :src="assetsApi.thumbnailUrl(asset.id, 160)" :alt="asset.original_name" loading="lazy" decoding="async" />
          <small>{{ asset.original_name }}</small>
        </button>
        <button
          v-if="multiple"
          type="button"
          class="asset-option-check"
          :aria-label="modelValue.includes(asset.id) ? `取消选择 ${asset.original_name}` : `加入多选：${asset.original_name}`"
          :aria-pressed="modelValue.includes(asset.id)"
          @click="toggle(asset)"
        >{{ modelValue.includes(asset.id) ? modelValue.indexOf(asset.id) + 1 : '' }}</button>
        <span v-else-if="modelValue.includes(asset.id)" class="asset-option-mark" aria-hidden="true">✓</span>
      </div>
    </div>
    <button v-if="filtered.length > shown.length" type="button" class="secondary-button asset-picker-more" @click="visibleCount += pageSize">
      显示更多（还有 {{ filtered.length - shown.length }} 张）
    </button>
  </div>
</template>
