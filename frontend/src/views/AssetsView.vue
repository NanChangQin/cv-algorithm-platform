<script setup lang="ts">
import { ref } from 'vue'
import { assetsApi } from '@/api/assets'
import { useAssets } from '@/composables/useAssets'

const { assets, loading, uploading, error, refresh, upload } = useAssets()
const selected = ref<string[]>([])

async function handleFiles(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  if (files.length) await upload(files)
  input.value = ''
}

function formatBytes(bytes: number) {
  return bytes < 1024 * 1024
    ? `${(bytes / 1024).toFixed(1)} KB`
    : `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
async function downloadSelected() {
  const blob = await assetsApi.download(selected.value)
  const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = 'cv-assets.zip'; link.click(); URL.revokeObjectURL(link.href)
}
</script>

<template>
  <header class="page-heading">
    <span class="eyebrow">CONTROLLED FILE STORAGE</span>
    <h1>资源中心</h1>
    <p>图片经过格式、MIME、尺寸与内容校验后存入本地受控目录，同一原图可供多个任务复用。</p>
  </header>
  <p v-if="error" class="alert">{{ error }}</p>
  <div class="toolbar">
    <span>{{ assets.length }} 张图片</span>
    <div class="toolbar-actions">
      <button class="secondary-button" @click="refresh">刷新</button>
      <button v-if="selected.length" class="secondary-button" @click="downloadSelected">下载 {{ selected.length }} 张 ZIP</button>
      <label class="primary-button upload-button">
        {{ uploading ? '上传中…' : '上传图片' }}
        <input
          type="file"
          multiple
          accept="image/jpeg,image/png,image/bmp,image/webp"
          :disabled="uploading"
          @change="handleFiles"
        />
      </label>
    </div>
  </div>
  <div v-if="loading" class="empty-state">正在加载…</div>
  <div v-else-if="assets.length === 0" class="empty-state">尚未上传图片</div>
  <div v-else class="asset-grid">
    <article v-for="asset in assets" :key="asset.id" class="asset-card">
      <label class="asset-selector"><input v-model="selected" type="checkbox" :value="asset.id" />选择</label>
      <img :src="assetsApi.contentUrl(asset.id)" :alt="asset.original_name" loading="lazy" />
      <div>
        <strong>{{ asset.original_name }}</strong>
        <span>{{ asset.width }} × {{ asset.height }} · {{ formatBytes(asset.size_bytes) }}</span>
        <small class="mono">{{ asset.sha256.slice(0, 14) }}…</small>
        <RouterLink class="asset-run-link" :to="{ path: '/', query: { asset: asset.id } }">用此图片推理 →</RouterLink>
      </div>
    </article>
  </div>
</template>
