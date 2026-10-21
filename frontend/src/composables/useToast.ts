import { reactive } from 'vue'

export type ToastKind = 'success' | 'error' | 'info'
export interface Toast { id: number; kind: ToastKind; message: string }

const toasts = reactive<Toast[]>([])
let nextId = 1

function dismiss(id: number) {
  const index = toasts.findIndex((item) => item.id === id)
  if (index >= 0) toasts.splice(index, 1)
}

function push(kind: ToastKind, message: string, timeout: number) {
  // 轮询等场景可能反复报同一个错误，已在显示的相同提示不再叠加
  const existing = toasts.find((item) => item.kind === kind && item.message === message)
  if (existing) return existing.id
  const id = nextId++
  toasts.push({ id, kind, message })
  if (toasts.length > 4) toasts.splice(0, toasts.length - 4)
  window.setTimeout(() => dismiss(id), timeout)
  return id
}

export const toast = {
  success: (message: string) => push('success', message, 3500),
  info: (message: string) => push('info', message, 4500),
  error: (message: string) => push('error', message, 8000),
}

export function useToasts() {
  return { toasts, dismiss }
}
