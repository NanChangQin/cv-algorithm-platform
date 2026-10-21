import { ApiError } from '@/api/http'

/** 取出可以直接展示给用户的错误文案：后端业务错误用其中文消息，其余异常用兜底文案。 */
export function errorMessage(reason: unknown, fallback: string): string {
  if (reason instanceof ApiError) return reason.message || fallback
  if (reason instanceof Error && /[一-龥]/.test(reason.message)) return reason.message
  return fallback
}
