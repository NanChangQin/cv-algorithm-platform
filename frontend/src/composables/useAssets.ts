import { assetsApi } from '@/api/assets'
import { ApiError } from '@/api/http'
import type { ImageAsset } from '@/types/asset'
import { errorMessage } from '@/utils/errors'

export const UPLOAD_BATCH_SIZE = 10

export interface UploadFailure { name: string; message: string }
export interface UploadOutcome { uploaded: ImageAsset[]; failed: UploadFailure[] }

/**
 * 分批上传（每批最多 10 张）。服务端一批里只要有一张不合格就整批失败，
 * 因此失败的批次会拆成单张重试，准确找出不合格的文件，其余照常上传。
 */
export async function uploadInBatches(
  files: File[],
  options: { onProgress?: (ratio: number) => void; onUploaded?: (assets: ImageAsset[]) => void } = {},
): Promise<UploadOutcome> {
  const outcome: UploadOutcome = { uploaded: [], failed: [] }
  const totalBytes = files.reduce((sum, file) => sum + file.size, 0) || 1
  let finishedBytes = 0
  const send = async (batch: File[]) => {
    const batchBytes = batch.reduce((sum, file) => sum + file.size, 0)
    const response = await assetsApi.upload(batch, (ratio) => {
      options.onProgress?.(Math.min(1, (finishedBytes + batchBytes * ratio) / totalBytes))
    })
    finishedBytes += batchBytes
    outcome.uploaded.push(...response.assets)
    options.onUploaded?.(response.assets)
  }
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
  return outcome
}

export function uploadFailureMessage(failed: UploadFailure[]): string {
  const first = failed[0]
  if (!first) return ''
  return failed.length === 1
    ? `${first.name} 上传失败：${first.message}`
    : `${failed.length} 张图片上传失败，例如 ${first.name}：${first.message}`
}
