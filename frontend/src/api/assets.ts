import { apiUrl, projectContext, queryString, request, requestBlob, requestPage, uploadRequest } from './http'
import type { BatchUploadResponse, ImageAsset } from '@/types/asset'

function withProject(path: string) {
  const projectId = projectContext.get()
  if (!projectId) return apiUrl(path)
  return apiUrl(`${path}${path.includes('?') ? '&' : '?'}project_id=${encodeURIComponent(projectId)}`)
}

export const assetsApi = {
  list: () => request<ImageAsset[]>('/assets'),
  /** 服务端分页，可按文件名搜索。 */
  page: (params: { q?: string; limit: number; offset?: number; sort?: 'newest' | 'name' }) =>
    requestPage<ImageAsset>(`/assets${queryString(params)}`),
  byIds: (ids: string[]) => ids.length
    ? request<ImageAsset[]>(`/assets${queryString({ ids })}`)
    : Promise.resolve([] as ImageAsset[]),
  get: (id: string) => request<ImageAsset>(`/assets/${id}`),
  contentUrl: (id: string) => withProject(`/assets/${id}/content`),
  /** 列表和选择器用缩略图（160/320/640），避免一次性下载所有原图。 */
  thumbnailUrl: (id: string, size: 160 | 320 | 640 = 320) => withProject(`/assets/${id}/thumbnail?size=${size}`),
  upload: (files: File[], onProgress?: (ratio: number) => void) => {
    const body = new FormData()
    for (const file of files) body.append('files', file)
    return uploadRequest<BatchUploadResponse>('/assets/upload', body, onProgress)
  },
  download: (ids: string[]) => requestBlob('/assets/download', {
    method: 'POST', body: JSON.stringify(ids),
  }),
}
