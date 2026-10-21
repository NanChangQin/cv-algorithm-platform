export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code = 'HTTP_ERROR',
  ) {
    super(message)
  }
}

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? '/api/v1'
const tokenKey = 'cv-platform-token'
const projectKey = 'cv-platform-project-id'

export interface Page<T> { items: T[]; total: number }

/** 拼接查询参数：空值跳过，数组按同名参数重复传递（例如 status=queued&status=running）。 */
export function queryString(params: Record<string, string | number | readonly string[] | null | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue
    if (Array.isArray(value)) for (const item of value) search.append(key, item)
    else search.set(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await send(path, init)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

/** 分页列表：当前页的数据，加上服务端在 X-Total-Count 里给出的总数。 */
export async function requestPage<T>(path: string): Promise<Page<T>> {
  const response = await send(path)
  const items = (await response.json()) as T[]
  const total = Number(response.headers.get('X-Total-Count') ?? items.length)
  return { items, total: Number.isFinite(total) ? total : items.length }
}

async function send(path: string, init?: RequestInit): Promise<Response> {
  const isFormData = init?.body instanceof FormData
  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...init,
    credentials: 'same-origin',
    headers: {
      Accept: 'application/json',
      ...(init?.body && !isFormData ? { 'Content-Type': 'application/json' } : {}),
      ...init?.headers,
      ...(localStorage.getItem(projectKey) ? { 'X-Project-ID': localStorage.getItem(projectKey)! } : {}),
    },
  })

  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as
      | { code?: string; message?: string }
      | null
    if (response.status === 401 && !path.startsWith('/auth/')) {
      localStorage.removeItem(tokenKey)
      if (window.location.pathname !== '/login') window.location.assign('/login')
    }
    throw new ApiError(payload?.message ?? '请求失败，请稍后重试', response.status, payload?.code)
  }
  return response
}

export async function requestBlob(path: string, init?: RequestInit): Promise<Blob> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...init,
    credentials: 'same-origin',
    headers: {
      Accept: 'application/octet-stream, application/zip',
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...init?.headers,
      ...(localStorage.getItem(projectKey) ? { 'X-Project-ID': localStorage.getItem(projectKey)! } : {}),
    },
  })
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { code?: string; message?: string } | null
    if (response.status === 401) {
      localStorage.removeItem(tokenKey)
      if (window.location.pathname !== '/login') window.location.assign('/login')
    }
    throw new ApiError(payload?.message ?? '文件下载失败', response.status, payload?.code)
  }
  return response.blob()
}

/** 带上传进度的 POST（fetch 无法报告上传进度）。onProgress 收到 0–1 的比例。 */
export function uploadRequest<T>(path: string, body: FormData, onProgress?: (ratio: number) => void): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${apiBaseUrl}${path}`)
    xhr.withCredentials = true
    xhr.setRequestHeader('Accept', 'application/json')
    const projectId = localStorage.getItem(projectKey)
    if (projectId) xhr.setRequestHeader('X-Project-ID', projectId)
    xhr.upload.onprogress = (event) => { if (event.lengthComputable) onProgress?.(event.loaded / event.total) }
    xhr.onload = () => {
      let payload: unknown = null
      try { payload = xhr.responseText ? JSON.parse(xhr.responseText) : null } catch { /* 非 JSON 响应按失败处理 */ }
      if (xhr.status >= 200 && xhr.status < 300) { resolve(payload as T); return }
      const error = payload as { code?: string; message?: string } | null
      if (xhr.status === 401) {
        localStorage.removeItem(tokenKey)
        if (window.location.pathname !== '/login') window.location.assign('/login')
      }
      reject(new ApiError(error?.message ?? (xhr.status === 413 ? '文件太大，超过服务器限制' : '上传失败，请稍后重试'), xhr.status, error?.code))
    }
    xhr.onerror = () => reject(new ApiError('网络连接中断，上传失败', 0, 'NETWORK_ERROR'))
    xhr.send(body)
  })
}

export function apiUrl(path: string): string {
  return `${apiBaseUrl}${path}`
}

export const apiBase = apiBaseUrl

export const sessionToken = {
  get: () => localStorage.getItem(tokenKey),
  set: () => localStorage.setItem(tokenKey, 'cookie-session'),
  clear: () => localStorage.removeItem(tokenKey),
}

export const projectContext = {
  get: () => localStorage.getItem(projectKey),
  set: (id: string) => localStorage.setItem(projectKey, id),
  clear: () => localStorage.removeItem(projectKey),
}
