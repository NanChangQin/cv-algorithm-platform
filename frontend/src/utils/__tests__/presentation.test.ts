import { describe, expect, it } from 'vitest'
import { nodeLabel, taskElapsed } from '../presentation'
import type { InferenceTask } from '@/types/task'

describe('task presentation', () => {
  it('uses readable node names while preserving unknown recorded ids', () => {
    expect(nodeLabel(null)).toBe('自动调度')
    expect(nodeLabel('local')).toBe('主服务器')
    expect(nodeLabel('dell', [{ id: 'dell', name: 'Dell GPU' }])).toBe('Dell GPU')
    expect(nodeLabel('retired-node')).toBe('retired-node')
  })
  it('distinguishes waiting, running and terminal task durations', () => {
    expect(taskElapsed({ started_at: null } as InferenceTask)).toBe('—')
    expect(taskElapsed({ started_at: '2026-10-16T00:00:00Z' } as InferenceTask)).toBe('执行中')
    expect(taskElapsed({ started_at: '2026-10-16T00:00:00Z', completed_at: '2026-10-16T00:00:01.500Z' } as InferenceTask)).toBe('1.5 s')
  })
})
