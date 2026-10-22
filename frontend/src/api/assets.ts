import { projectUrl, queryString, request, requestBlob, requestPage, uploadRequest } from './http'
import type { BatchUploadResponse, ImageAsset } from '@/types/asset'

export const assetsApi = {
  /** 服务端分页，可按文件名搜索。 */
  page: (params: { q?: string; limit: number; offset?: number; sort?: 'newest' | 'name' }) =>
    requestPage<ImageAsset>(`/assets${queryString(params)}`),
  byIds: (ids: string[]) => ids.length
    ? request<ImageAsset[]>(`/assets${queryString({ ids })}`)
    : Promise.resolve([] as ImageAsset[]),
  get: (id: string) => request<ImageAsset>(`/assets/${id}`),
  contentUrl: (id: string) => projectUrl(`/assets/${id}/content`),
  /** 列表和选择器用缩略图（160/320/640），避免一次性下载所有原图。 */
  thumbnailUrl: (id: string, size: 160 | 320 | 640 = 320) => projectUrl(`/assets/${id}/thumbnail?size=${size}`),
  upload: (files: File[], onProgress?: (ratio: number) => void) => {
    const body = new FormData()
    for (const file of files) body.append('files', file)
    return uploadRequest<BatchUploadResponse>('/assets/upload', body, onProgress)
  },
  download: (ids: string[]) => requestBlob('/assets/download', {
    method: 'POST', body: JSON.stringify(ids),
  }),
}
