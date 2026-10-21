import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ get: vi.fn(), result: vi.fn(), subscribe: vi.fn() }))
vi.mock('@/api/tasks', () => ({ tasksApi: api }))
import { useTaskWatch } from '../useTaskWatch'

function setup(id: string) {
  const taskId = ref(id)
  let state!: ReturnType<typeof useTaskWatch>
  const wrapper = mount(defineComponent({ setup() { state = useTaskWatch(taskId); return () => null } }))
  return { taskId, state, wrapper }
}

describe('useTaskWatch', () => {
  beforeEach(() => vi.clearAllMocks())

  it('subscribes while running and reads the result once the task completes', async () => {
    const source = { close: vi.fn() }
    api.subscribe.mockReturnValue(source)
    api.get.mockResolvedValueOnce({ id: 't1', status: 'running' }).mockResolvedValueOnce({ id: 't1', status: 'completed' })
    api.result.mockResolvedValue({ result: { type: 'classification' } })
    const { state, wrapper } = setup('t1')
    await flushPromises()
    expect(api.subscribe).toHaveBeenCalledWith('t1', expect.any(Function))
    api.subscribe.mock.calls[0]![1]({ status: 'completed' })
    await flushPromises()
    expect(state.result.value).toEqual({ type: 'classification' })
    expect(state.terminal.value).toBe(true)
    expect(source.close).toHaveBeenCalled()
    wrapper.unmount()
  })

  it('does nothing without a task id and starts over when the id changes', async () => {
    api.get.mockResolvedValue({ id: 't2', status: 'failed' })
    const { taskId, state, wrapper } = setup('')
    await flushPromises()
    expect(api.get).not.toHaveBeenCalled()
    taskId.value = 't2'
    await flushPromises()
    expect(state.task.value?.status).toBe('failed')
    expect(api.subscribe).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
