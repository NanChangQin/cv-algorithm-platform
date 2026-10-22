<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { assetsApi } from '@/api/assets'
import { useAssetPages } from '@/composables/useAssetPages'
import type { ImageAsset } from '@/types/asset'

const props = withDefaults(defineProps<{
  modelValue: ImageAsset | null
  pageSize?: number
  /** 没有选中图片时自动选最新的一张 */
  selectFirst?: boolean
}>(), { pageSize: 24, selectFirst: false })
const emit = defineEmits<{ 'update:modelValue': [value: ImageAsset] }>()

// 按页向服务端读取，搜索也交给服务端：图片再多，打开时也只读一页
const library = useAssetPages(props.pageSize)
const { items, total, query, loading, error } = library
/**
 * 固定在最前面的图片：刚上传的、链接带过来但不在已读取页面里的、在搜索结果里选中的。
 * 不搜索时显示在最前面，后面的页面里再读到时不重复显示，位置也不会跳动。
 */
const pinned = ref<ImageAsset[]>([])
const shown = computed(() => {
  if (query.value.trim()) return items.value
  const front = new Set(pinned.value.map((asset) => asset.id))
  return [...pinned.value, ...items.value.filter((asset) => !front.has(asset.id))]
})
const remaining = computed(() => total.value - shown.value.length)
/** 搜索框出现后就一直保留，避免清空搜索词时因结果变少而消失、丢失焦点 */
const searchable = ref(false)

watch(total, (value) => { if (value > 6) searchable.value = true })
watch(() => props.modelValue, (asset) => {
  if (!asset || pinned.value.some((item) => item.id === asset.id)) return
  if (query.value.trim() || !items.value.some((item) => item.id === asset.id)) pinned.value = [asset, ...pinned.value]
}, { immediate: true })
watch(items, (loaded) => {
  if (props.selectFirst && !props.modelValue && !query.value.trim() && loaded[0]) emit('update:modelValue', loaded[0])
})

/** 刚上传的图片放到最前面，并清空搜索词让它显示出来 */
function add(assets: ImageAsset[]) {
  const known = new Set([...pinned.value, ...items.value].map((asset) => asset.id))
  const added = assets.filter((asset) => !known.has(asset.id))
  pinned.value = [...added, ...pinned.value]
  // 去重上传可能返回库中已有的图片，不能把所有不在当前页的图都当作新增。
  // 重新读取第一页，既校准总数，也避免新图片插入后下一页 offset 错位。
  if (query.value) query.value = ''
  else void library.refresh()
}
defineExpose({ add })
</script>

<template>
  <div class="asset-picker">
    <div v-if="searchable" class="asset-picker-tools">
      <input v-model="query" type="search" placeholder="按文件名搜索图片" aria-label="按文件名搜索图片" @keydown.enter.prevent />
    </div>
    <p v-if="error" class="alert" role="alert">{{ error }}</p>
    <p v-else-if="!shown.length" class="muted-copy">
      {{ loading ? '正在加载图片…' : query.trim() ? `没有文件名包含“${query.trim()}”的图片。` : '图片库里还没有图片。' }}
    </p>
    <div v-if="shown.length" class="asset-picker-grid" aria-label="选择输入图片">
      <div v-for="asset in shown" :key="asset.id" class="asset-option" :class="{ selected: asset.id === modelValue?.id }">
        <button
          type="button"
          class="asset-option-main"
          :aria-pressed="asset.id === modelValue?.id"
          :title="`${asset.original_name} · ${asset.width} × ${asset.height}`"
          @click="emit('update:modelValue', asset)"
        >
          <img :src="assetsApi.thumbnailUrl(asset.id, 160)" :alt="asset.original_name" loading="lazy" decoding="async" />
          <small>{{ asset.original_name }}</small>
        </button>
        <span v-if="asset.id === modelValue?.id" class="asset-option-mark" aria-hidden="true">✓</span>
      </div>
    </div>
    <button v-if="remaining > 0" type="button" class="secondary-button asset-picker-more" :disabled="loading" @click="library.loadMore()">
      {{ loading ? '正在加载…' : `显示更多（还有 ${remaining} 张）` }}
    </button>
  </div>
</template>
