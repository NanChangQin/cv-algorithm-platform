import { onMounted, ref } from 'vue'

import { assetsApi } from '@/api/assets'
import { ApiError } from '@/api/http'
import type { ImageAsset } from '@/types/asset'
import { errorMessage } from '@/utils/errors'

export const UPLOAD_BATCH_SIZE = 10

export interface UploadFailure { name: string; message: string }
export interface UploadOutcome { uploaded: ImageAsset[]; failed: UploadFailure[] }

export function useAssets() {
  const assets = ref<ImageAsset[]>([])
  const loading = ref(false)
  const uploading = ref(false)
  /** 上传进度 0–1；未上传时为 null */
  const uploadProgress = ref<number | null>(null)
  const error = ref('')

  async function refresh() {
    loading.value = true
    error.value = ''
    try {
      assets.value = await assetsApi.list()
    } catch (reason) {
      error.value = errorMessage(reason, '加载图片失败')
    } finally {
      loading.value = false
    }
  }

  function merge(items: ImageAsset[]) {
    const byId = new Map(assets.value.map((asset) => [asset.id, asset]))
    for (const asset of items) byId.set(asset.id, asset)
    assets.value = [...byId.values()].sort((a, b) => b.created_at.localeCompare(a.created_at))
  }

  /**
   * 分批上传（每批最多 10 张）。服务端一批里只要有一张不合格就整批失败，
   * 因此失败的批次会拆成单张重试，准确找出不合格的文件，其余照常上传。
   */
  async function upload(files: File[]): Promise<UploadOutcome> {
    uploading.value = true
    uploadProgress.value = 0
    error.value = ''
    const outcome: UploadOutcome = { uploaded: [], failed: [] }
    const totalBytes = files.reduce((sum, file) => sum + file.size, 0) || 1
    let finishedBytes = 0
    const send = async (batch: File[]) => {
      const batchBytes = batch.reduce((sum, file) => sum + file.size, 0)
      const response = await assetsApi.upload(batch, (ratio) => {
        uploadProgress.value = Math.min(1, (finishedBytes + batchBytes * ratio) / totalBytes)
      })
      finishedBytes += batchBytes
      outcome.uploaded.push(...response.assets)
      merge(response.assets)
    }
    try {
      for (let start = 0; start < files.length; start += UPLOAD_BATCH_SIZE) {
        const batch = files.slice(start, start + UPLOAD_BATCH_SIZE)
        try {
          await send(batch)
        } catch (reason) {
          const rejectedByServer = reason instanceof ApiError && reason.status >= 400 && reason.status < 500 && reason.status !== 429
          if (!rejectedByServer || batch.length === 1) {
            for (const file of batch) outcome.failed.push({ name: file.name, message: errorMessage(reason, '上传失败') })
            finishedBytes += batch.reduce((sum, file) => sum + file.size, 0)
            continue
          }
          for (const file of batch) {
            try { await send([file]) } catch (single) {
              finishedBytes += file.size
              outcome.failed.push({ name: file.name, message: errorMessage(single, '上传失败') })
            }
          }
        }
      }
      if (outcome.failed.length) {
        const first = outcome.failed[0]!
        error.value = outcome.failed.length === 1
          ? `${first.name} 上传失败：${first.message}`
          : `${outcome.failed.length} 张图片上传失败，例如 ${first.name}：${first.message}`
      }
      return outcome
    } finally {
      uploading.value = false
      uploadProgress.value = null
    }
  }

  onMounted(refresh)
  return { assets, loading, uploading, uploadProgress, error, refresh, upload }
}
