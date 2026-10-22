import { flushPromises, shallowMount } from '@vue/test-utils'
import { ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const calls = vi.hoisted(() => ({
  create: vi.fn(), resources: vi.fn(), getTask: vi.fn(), result: vi.fn(), page: vi.fn(), byIds: vi.fn(),
  query: {} as Record<string, string>, parameters: {} as Record<string, unknown>,
  libraryItems: null as null | { value: Array<{ id: string; original_name: string; width: number; height: number }> },
}))
vi.mock('vue-router', () => ({ useRoute: () => ({ query: calls.query }) }))
vi.mock('@/api/tasks', () => ({ tasksApi: {
  get: calls.getTask, create: calls.create, result: calls.result, page: calls.page, byIds: calls.byIds, subscribe: vi.fn(),
} }))
vi.mock('@/api/assets', () => ({ assetsApi: { thumbnailUrl: () => '', contentUrl: () => '', byIds: vi.fn().mockResolvedValue([]) } }))
vi.mock('@/api/system', () => ({ systemApi: { executionResources: calls.resources } }))
vi.mock('@/composables/useAlgorithms', () => ({ useAlgorithms: () => {
  const algorithms = ref([{ id: 'algorithm-1', name: 'Detector', device: 'gpu', parameters: calls.parameters, status: 'available' }])
  return { algorithms, availableAlgorithms: algorithms, loading: ref(false), error: ref('') }
} }))
vi.mock('@/composables/useAssetPages', () => ({ useAssetPages: () => {
  const items = ref([
    { id: 'asset-1', original_name: 'image.jpg', width: 10, height: 10 },
    { id: 'asset-2', original_name: 'second.jpg', width: 10, height: 10 },
  ])
  calls.libraryItems = items
  return {
    items,
    total: ref(2), query: ref(''), loading: ref(false), error: ref(''), hasMore: ref(false),
    refresh: vi.fn(), loadMore: vi.fn(), prepend: vi.fn(), remove: vi.fn(),
  }
} }))

import { ApiError } from '@/api/http'
import ResultViewer from '@/components/results/ResultViewer.vue'
import ImageFilmstrip from '@/components/workbench/ImageFilmstrip.vue'
import WorkspaceView from '../WorkspaceView.vue'

const stubs = { RouterLink: true }
const detection = { type: 'object_detection', data: { detections: [] }, input: { width: 10, height: 10 }, timing: {} }

describe('workbench', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    calls.query = {}
    calls.parameters = {}
    calls.create.mockResolvedValue({ id: 'task-1', status: 'queued' })
    calls.getTask.mockResolvedValue({ id: 'task-1', status: 'queued', algorithm_version_id: 'algorithm-1', asset_id: 'asset-1', parameters: {}, gpu_device_ids: [], created_at: '2026-10-22T00:00:00Z' })
    calls.page.mockResolvedValue({ items: [], total: 0 })
    calls.byIds.mockResolvedValue([])
    calls.result.mockResolvedValue({ result: detection })
    calls.resources.mockResolvedValue({
      nodes: [{ id: 'local', name: 'Platform server' }, { id: 'dell', name: 'Dell GPU' }],
      gpus: [{ node_id: 'dell', node_name: 'Dell GPU', index: 0, name: 'RTX 5070 Ti', memory_total_mb: 16303, memory_used_mb: 18 }],
    })
  })

  it('submits the selected node and card, then follows the new task in place', async () => {
    const wrapper = shallowMount(WorkspaceView, { global: { stubs } })
    await flushPromises()
    await wrapper.find('fieldset select').setValue('dell')
    expect(wrapper.find('fieldset').text()).toContain('RTX 5070 Ti')
    await wrapper.findAll('fieldset select')[1]!.setValue('0')
    await wrapper.find('button.run-button').trigger('click')
    await flushPromises()
    expect(calls.create).toHaveBeenCalledWith({ algorithm_version_id: 'algorithm-1', asset_id: 'asset-1', parameters: {}, node_id: 'dell', gpu_device_ids: ['0'] })
    expect(calls.getTask).toHaveBeenCalledWith('task-1')
    expect(wrapper.text()).toContain('image.jpg')
  })

  it('keeps the selected input runnable when a search replaces the visible image page', async () => {
    const wrapper = shallowMount(WorkspaceView, { global: { stubs } })
    await flushPromises()
    expect(wrapper.text()).toContain('image.jpg')
    calls.libraryItems!.value = [{ id: 'asset-2', original_name: 'second.jpg', width: 10, height: 10 }]
    await flushPromises()
    expect(wrapper.text()).toContain('image.jpg')
    expect(wrapper.find('button.run-button').attributes('disabled')).toBeUndefined()
    await wrapper.find('button.run-button').trigger('click')
    expect(calls.create).toHaveBeenCalledWith(expect.objectContaining({ asset_id: 'asset-1' }))
    wrapper.unmount()
  })

  it('clears a card when switching back to automatic scheduling', async () => {
    const wrapper = shallowMount(WorkspaceView, { global: { stubs } })
    await flushPromises()
    await wrapper.find('fieldset select').setValue('dell')
    await wrapper.findAll('fieldset select')[1]!.setValue('0')
    await wrapper.find('fieldset select').setValue('')
    await wrapper.find('button.run-button').trigger('click')
    expect(calls.create).toHaveBeenCalledWith(expect.objectContaining({ node_id: undefined, gpu_device_ids: [] }))
  })

  it('restores the original task node and GPU before rerunning', async () => {
    calls.query = { from_task: 'original-task' }
    calls.getTask.mockResolvedValue({ algorithm_version_id: 'algorithm-1', asset_id: 'asset-1', parameters: {}, node_id: 'dell', gpu_device_ids: ['0'] })
    const wrapper = shallowMount(WorkspaceView, { global: { stubs } })
    await flushPromises()
    expect((wrapper.find('fieldset select').element as HTMLSelectElement).value).toBe('dell')
    expect((wrapper.findAll('fieldset select')[1]!.element as HTMLSelectElement).value).toBe('0')
    await wrapper.find('button.run-button').trigger('click')
    expect(calls.create).toHaveBeenCalledWith(expect.objectContaining({ asset_id: 'asset-1', node_id: 'dell', gpu_device_ids: ['0'] }))
  })

  it('blocks invalid numeric parameters before submitting', async () => {
    calls.parameters = { confidence: { type: 'number', title: '置信度', default: 0.5, minimum: 0, maximum: 1 } }
    const wrapper = shallowMount(WorkspaceView, { global: { stubs: { ...stubs, ParameterForm: false } } })
    await flushPromises()
    await wrapper.find('input[type="number"]').setValue('2')
    expect(wrapper.text()).toContain('置信度不能大于 1')
    expect(wrapper.find('button.run-button').attributes('disabled')).toBeDefined()
    expect(calls.create).not.toHaveBeenCalled()
  })

  it('keeps input and placement when submission fails', async () => {
    calls.create.mockRejectedValue(new Error('network unavailable'))
    const wrapper = shallowMount(WorkspaceView, { global: { stubs } })
    await flushPromises()
    await wrapper.find('fieldset select').setValue('dell')
    await wrapper.find('button.run-button').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('创建任务失败')
    expect((wrapper.find('fieldset select').element as HTMLSelectElement).value).toBe('dell')
    expect(wrapper.text()).toContain('image.jpg')
  })

  it('submits one task per selected image and shows the batch progress in place', async () => {
    calls.create.mockResolvedValueOnce({ id: 'task-1', status: 'queued' }).mockResolvedValueOnce({ id: 'task-2', status: 'queued' })
    const wrapper = shallowMount(WorkspaceView, { global: { stubs } })
    await flushPromises()
    wrapper.findComponent(ImageFilmstrip).vm.$emit('update:selected', ['asset-1', 'asset-2'])
    await flushPromises()
    expect(wrapper.find('button.run-button').text()).toContain('批量推理 2 张图片')
    await wrapper.find('button.run-button').trigger('click')
    await flushPromises()
    expect(calls.create.mock.calls.map(([payload]) => payload.asset_id)).toEqual(['asset-1', 'asset-2'])
    expect(wrapper.find('.batch-progress').text()).toContain('已提交 2/2')
    expect(wrapper.findComponent(ImageFilmstrip).props('statuses')).toEqual({ 'asset-1': 'queued', 'asset-2': 'queued' })
  })

  it('waits and retries when the platform rate-limits a batch', async () => {
    vi.useFakeTimers()
    try {
      calls.create
        .mockRejectedValueOnce(new ApiError('请求过于频繁，请稍后重试', 429, 'RATE_LIMITED'))
        .mockResolvedValue({ id: 'task-1', status: 'queued' })
      const wrapper = shallowMount(WorkspaceView, { global: { stubs } })
      await flushPromises()
      wrapper.findComponent(ImageFilmstrip).vm.$emit('update:selected', ['asset-1', 'asset-2'])
      await flushPromises()
      await wrapper.find('button.run-button').trigger('click')
      await flushPromises()
      expect(calls.create).toHaveBeenCalledTimes(1)
      await vi.advanceTimersByTimeAsync(3000)
      await flushPromises()
      expect(calls.create).toHaveBeenCalledTimes(3)
      expect(wrapper.find('.batch-progress').text()).toContain('已提交 2/2')
    } finally { vi.useRealTimers() }
  })

  it('does not wait for server discovery when scheduling automatically', async () => {
    calls.resources.mockReturnValue(new Promise(() => {}))
    const wrapper = shallowMount(WorkspaceView, { global: { stubs } })
    await flushPromises()
    expect(wrapper.find('button.run-button').attributes('disabled')).toBeUndefined()
    await wrapper.find('button.run-button').trigger('click')
    expect(calls.create).toHaveBeenCalledWith(expect.objectContaining({ asset_id: 'asset-1', node_id: undefined }))
  })

  it('shows the latest result of the focused image for the selected algorithm', async () => {
    const earlier = { id: 'done-task', status: 'completed', algorithm_version_id: 'algorithm-1', asset_id: 'asset-1', parameters: {}, gpu_device_ids: [], created_at: '2026-10-21T00:00:00Z' }
    calls.page.mockResolvedValue({ items: [earlier], total: 1 })
    calls.getTask.mockResolvedValue(earlier)
    const wrapper = shallowMount(WorkspaceView, { global: { stubs } })
    await flushPromises()
    expect(calls.page).toHaveBeenCalledWith({ asset_id: 'asset-1', limit: 20 })
    expect(calls.getTask).toHaveBeenCalledWith('done-task')
    expect(wrapper.findComponent(ResultViewer).props('result')).toEqual(detection)
  })
})
