<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref } from 'vue'

import { assetsApi } from '@/api/assets'
import UploadDropzone from '@/components/assets/UploadDropzone.vue'
import { useAlgorithms } from '@/composables/useAlgorithms'
import { useAssetPages } from '@/composables/useAssetPages'
import { uploadInBatches, type UploadFailure } from '@/composables/useAssets'
import { toast } from '@/composables/useToast'
import type { ImageAsset } from '@/types/asset'
import { errorMessage } from '@/utils/errors'
import { formatBytes, formatDateTime } from '@/utils/presentation'

const MAX_SELECTION = 100
const library = useAssetPages(48)
const { items: assets, total, query, sort: sortBy, loading, error, hasMore, refresh, loadMore } = library
const { availableAlgorithms } = useAlgorithms()
const selected = ref<string[]>([])
const uploading = ref(false)
const uploadProgress = ref<number | null>(null)
const downloading = ref(false)
const failures = ref<UploadFailure[]>([])
const previewIndex = ref<number | null>(null)
const closeButton = ref<HTMLButtonElement | null>(null)
const preview = computed<ImageAsset | null>(() => previewIndex.value === null ? null : assets.value[previewIndex.value] ?? null)

async function handleFiles(files: File[]) {
  failures.value = []
  uploading.value = true
  uploadProgress.value = 0
  try {
    const outcome = await uploadInBatches(files, { onProgress: (ratio) => { uploadProgress.value = ratio } })
    failures.value = outcome.failed
    if (outcome.uploaded.length) {
      library.prepend(outcome.uploaded)
      toast.success(`已上传 ${outcome.uploaded.length} 张图片`)
    }
  } finally {
    uploading.value = false
    uploadProgress.value = null
  }
}
function toggle(id: string) {
  if (selected.value.includes(id)) selected.value = selected.value.filter((item) => item !== id)
  else if (selected.value.length < MAX_SELECTION) selected.value = [...selected.value, id]
  else toast.info(`一次最多选择 ${MAX_SELECTION} 张`)
}
function selectLoaded() {
  selected.value = [...new Set([...selected.value, ...assets.value.map((asset) => asset.id)])].slice(0, MAX_SELECTION)
}
async function downloadSelected() {
  downloading.value = true
  try {
    const blob = await assetsApi.download(selected.value)
    const link = document.createElement('a')
    link.href = URL.createObjectURL(blob)
    link.download = 'cv-assets.zip'
    link.click()
    URL.revokeObjectURL(link.href)
  } catch (reason) {
    toast.error(errorMessage(reason, '打包下载失败'))
  } finally { downloading.value = false }
}

async function openPreview(asset: ImageAsset) {
  previewIndex.value = assets.value.findIndex((item) => item.id === asset.id)
  await nextTick()
  closeButton.value?.focus()
}
function step(offset: number) {
  if (previewIndex.value === null || !assets.value.length) return
  previewIndex.value = (previewIndex.value + offset + assets.value.length) % assets.value.length
}
function onKeydown(event: KeyboardEvent) {
  if (previewIndex.value === null) return
  if (event.key === 'Escape') previewIndex.value = null
  else if (event.key === 'ArrowRight') step(1)
  else if (event.key === 'ArrowLeft') step(-1)
}
window.addEventListener('keydown', onKeydown)
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <header class="page-heading page-heading-actions">
    <div><h1>图片库</h1><p>图片经过格式、尺寸与内容校验后存入受控目录，同一张图可以被多个任务复用。</p></div>
    <button class="secondary-button" :disabled="loading" @click="refresh">{{ loading ? '刷新中…' : '刷新' }}</button>
  </header>
  <UploadDropzone :disabled="uploading" :progress="uploadProgress" accept-paste @files="handleFiles" />
  <div v-if="failures.length" class="alert" role="alert">
    <strong>{{ failures.length }} 张图片没有上传成功</strong>
    <ul class="failure-list"><li v-for="item in failures.slice(0, 8)" :key="item.name"><span class="mono">{{ item.name }}</span>：{{ item.message }}</li></ul>
    <button class="text-button" @click="failures = []">知道了</button>
  </div>
  <p v-else-if="error" class="alert" role="alert">{{ error }}</p>

  <div class="asset-toolbar">
    <input v-model="query" type="search" placeholder="按文件名搜索" aria-label="按文件名搜索图片" />
    <select v-model="sortBy" aria-label="排序方式"><option value="newest">最新上传在前</option><option value="name">按文件名</option></select>
    <span class="muted-copy">{{ query ? `匹配 ${total} 张` : `共 ${total} 张图片` }}<template v-if="assets.length < total">，已显示 {{ assets.length }} 张</template></span>
  </div>
  <div v-if="selected.length" class="selection-bar" role="region" aria-label="已选图片操作">
    <strong>已选 {{ selected.length }} 张</strong>
    <button class="text-button" @click="selected = []">清空选择</button>
    <span class="selection-bar-actions">
      <RouterLink v-if="availableAlgorithms.length" class="primary-button compact-button" :to="{ path: '/', query: { assets: selected.slice(0, 50).join(',') } }">{{ selected.length > 50 ? '用前 50 张推理 →' : '用选中图片推理 →' }}</RouterLink>
      <button class="secondary-button" :disabled="downloading" @click="downloadSelected">{{ downloading ? '正在打包…' : '下载 ZIP' }}</button>
    </span>
  </div>

  <div v-if="loading && !assets.length" class="empty-state">正在加载…</div>
  <div v-else-if="!assets.length && query" class="empty-state"><strong>没有匹配的图片</strong><p>换个关键词试试。</p></div>
  <div v-else-if="!assets.length" class="empty-state"><strong>还没有图片</strong><p>把图片拖到上方区域，或直接粘贴截图即可上传。</p></div>
  <template v-else>
    <div class="list-footer asset-list-head">
      <button class="text-button" @click="selectLoaded">{{ assets.length < total ? `选择已显示的 ${assets.length} 张` : query ? '选择全部搜索结果' : '全选' }}</button>
    </div>
    <div class="asset-grid">
      <article v-for="asset in assets" :key="asset.id" class="asset-card" :class="{ selected: selected.includes(asset.id) }">
        <label class="asset-selector"><input type="checkbox" :checked="selected.includes(asset.id)" :aria-label="`选择 ${asset.original_name}`" @change="toggle(asset.id)" />选择</label>
        <button type="button" class="asset-thumb" :aria-label="`预览 ${asset.original_name}`" @click="openPreview(asset)">
          <img :src="assetsApi.thumbnailUrl(asset.id, 320)" :alt="asset.original_name" loading="lazy" decoding="async" />
        </button>
        <div>
          <strong :title="asset.original_name">{{ asset.original_name }}</strong>
          <span>{{ asset.width }} × {{ asset.height }} · {{ formatBytes(asset.size_bytes) }}</span>
          <small>{{ formatDateTime(asset.created_at) }}</small>
          <RouterLink v-if="availableAlgorithms.length" class="asset-run-link" :to="{ path: '/', query: { asset: asset.id } }">用此图片推理 →</RouterLink>
        </div>
      </article>
    </div>
    <button v-if="hasMore" class="secondary-button load-more" :disabled="loading" @click="loadMore">{{ loading ? '正在加载…' : `显示更多（还有 ${total - assets.length} 张）` }}</button>
  </template>

  <div v-if="preview" class="lightbox" role="dialog" aria-modal="true" :aria-label="`预览 ${preview.original_name}`" @click.self="previewIndex = null">
    <div class="lightbox-body">
      <header>
        <div><strong>{{ preview.original_name }}</strong><small>{{ preview.width }} × {{ preview.height }} · {{ formatBytes(preview.size_bytes) }} · {{ formatDateTime(preview.created_at) }}</small></div>
        <button ref="closeButton" type="button" class="icon-button" aria-label="关闭预览" @click="previewIndex = null">×</button>
      </header>
      <div class="lightbox-stage">
        <button v-if="assets.length > 1" type="button" class="lightbox-nav prev" aria-label="上一张" @click="step(-1)">‹</button>
        <img :src="assetsApi.contentUrl(preview.id)" :alt="preview.original_name" />
        <button v-if="assets.length > 1" type="button" class="lightbox-nav next" aria-label="下一张" @click="step(1)">›</button>
      </div>
      <footer>
        <small class="mono">SHA-256 {{ preview.sha256.slice(0, 16) }}…</small>
        <span class="toolbar-actions">
          <a class="secondary-button" :href="assetsApi.contentUrl(preview.id)" :download="preview.original_name">下载原图</a>
          <RouterLink v-if="availableAlgorithms.length > 1" class="secondary-button" :to="{ path: '/compare', query: { asset: preview.id } }">多算法对比</RouterLink>
          <RouterLink v-if="availableAlgorithms.length" class="primary-button compact-button" :to="{ path: '/', query: { asset: preview.id } }">用此图片推理 →</RouterLink>
        </span>
      </footer>
    </div>
  </div>
</template>
