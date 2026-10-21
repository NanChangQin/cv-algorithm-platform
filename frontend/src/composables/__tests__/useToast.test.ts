import { describe, expect, it, vi } from 'vitest'

import { toast, useToasts } from '../useToast'

describe('toast', () => {
  it('does not stack an identical message that is still visible', () => {
    vi.useFakeTimers()
    const { toasts } = useToasts()
    toast.error('算法容器管理服务不可用')
    toast.error('算法容器管理服务不可用')
    toast.success('已保存')
    expect(toasts.map((item) => item.message)).toEqual(['算法容器管理服务不可用', '已保存'])
    vi.advanceTimersByTime(10_000)
    expect(toasts).toHaveLength(0)
    vi.useRealTimers()
  })
})
