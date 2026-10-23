import { flushPromises, shallowMount } from '@vue/test-utils'
import { ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const calls = vi.hoisted(() => ({
  create: vi.fn(), resources: vi.fn(), deployments: vi.fn(), routes: vi.fn(), getTask: vi.fn(), result: vi.fn(), page: vi.fn(), byIds: vi.fn(),
  query: {} as Record<string, string>, parameters: {} as Record<string, unknown>,
  algorithmsLoaded: true, algorithmList: null as null | { value: unknown[] },
  libraryItems: null as null | { value: Array<{ id: string; original_name: string; width: number; height: number }> },
}))
vi.mock('vue-router', () => ({ useRoute: () => ({ query: calls.query }) }))
vi.mock('@/api/tasks', () => ({ tasksApi: {
  get: calls.getTask, create: calls.create, result: calls.result, page: calls.page, byIds: calls.byIds, subscribe: vi.fn(),
} }))
vi.mock('@/api/assets', () => ({ assetsApi: { thumbnailUrl: () => '', contentUrl: () => '', byIds: vi.fn().mockResolvedValue([]) } }))
vi.mock('@/api/system', () => ({ systemApi: { executionResources: calls.resources } }))
vi.mock('@/api/deployments', () => ({ deploymentsApi: { list: calls.deployments, listRoutes: calls.routes } }))
vi.mock('@/composables/useAlgorithms', () => ({ useAlgorithms: () => {
  const algorithms = ref(calls.algorithmsLoaded === false ? [] : [{ id: 'algorithm-1', name: 'Detector', device: 'gpu', parameters: calls.parameters, status: 'available' }])
  calls.algorithmList = algorithms
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
    calls.algorithmsLoaded = true
    calls.create.mockResolvedValue({ id: 'task-1', status: 'queued' })
    calls.getTask.mockResolvedValue({ id: 'task-1', status: 'queued', algorithm_version_id: 'algorithm-1', asset_id: 'asset-1', parameters: {}, gpu_device_ids: [], created_at: '2026-10-22T00:00:00Z' })
    calls.page.mockResolvedValue({ items: [], total: 0 })
    calls.deployments.mockResolvedValue([])
    calls.routes.mockResolvedValue([])
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

  it('runs on a deployed instance chosen from the deployments page', async () => {
    calls.query = { algorithm: 'algorithm-1', deployment: 'deploy-1' }
    calls.deployments.mockResolvedValue([
      { id: 'deploy-1', algorithm_version_id: 'algorithm-1', node_id: 'dell', gpu_device_ids: ['0'], status: 'running' },
      { id: 'deploy-2', algorithm_version_id: 'algorithm-1', node_id: 'local', gpu_device_ids: [], status: 'stopped' },
      { id: 'deploy-3', algorithm_version_id: 'other', node_id: 'local', gpu_device_ids: [], status: 'running' },
    ])
    const wrapper = shallowMount(WorkspaceView, { global: { stubs } })
    await flushPromises()
    const options = wrapper.find('fieldset select').findAll('option').map((option) => option.text())
    expect(options).toEqual(['不指定', 'Dell GPU · GPU 0'])
    expect(wrapper.find('.placement-section summary').text()).toContain('部署实例 · Dell GPU · GPU 0')
    expect(wrapper.text()).not.toContain('执行服务器')
    await wrapper.find('button.run-button').trigger('click')
    await flushPromises()
    expect(calls.create).toHaveBeenCalledWith({
      algorithm_version_id: 'algorithm-1', asset_id: 'asset-1', parameters: {}, node_id: undefined, gpu_device_ids: [], deployment_id: 'deploy-1',
    })
  })

  it('keeps the linked deployment when the algorithm list arrives before the deployments', async () => {
    calls.query = { algorithm: 'algorithm-1', deployment: 'deploy-1' }
    calls.algorithmsLoaded = false
    let release: (value: unknown) => void = () => {}
    calls.deployments.mockReturnValue(new Promise((resolve) => { release = resolve }))
    const wrapper = shallowMount(WorkspaceView, { global: { stubs } })
    await flushPromises()
    calls.algorithmList!.value = [{ id: 'algorithm-1', name: 'Detector', device: 'gpu', parameters: {}, status: 'available' }]
    await flushPromises()
    release([{ id: 'deploy-1', algorithm_version_id: 'algorithm-1', node_id: 'dell', gpu_device_ids: ['0'], status: 'running' }])
    await flushPromises()
    expect(wrapper.find('.placement-section summary').text()).toContain('部署实例 · Dell GPU · GPU 0')
  })

  it('refuses to run on a deployment that stopped', async () => {
    calls.query = { algorithm: 'algorithm-1', deployment: 'deploy-2' }
    calls.deployments.mockResolvedValue([
      { id: 'deploy-2', algorithm_version_id: 'algorithm-1', node_id: 'local', gpu_device_ids: [], status: 'stopped' },
    ])
    const wrapper = shallowMount(WorkspaceView, { global: { stubs } })
    await flushPromises()
    expect(wrapper.text()).toContain('所选部署实例已不在运行')
    expect(wrapper.find('button.run-button').attributes('disabled')).toBeDefined()
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

  it('reuses the exact deployment with its defaults and the original task overrides', async () => {
    calls.query = { from_task: 'original-task' }
    calls.parameters = {
      confidence: { type: 'number', default: 0.5, minimum: 0, maximum: 1 },
      iou_threshold: { type: 'number', default: 0.45, minimum: 0, maximum: 1 },
    }
    calls.deployments.mockResolvedValue([{
      id: 'deploy-1', algorithm_version_id: 'algorithm-1', node_id: 'dell', gpu_device_ids: ['0'], status: 'running',
      runtime_profile: { defaults: { confidence: 0.6, iou_threshold: 0.7 }, allowed_overrides: ['confidence'] },
    }])
    calls.getTask.mockResolvedValue({ algorithm_version_id: 'algorithm-1', asset_id: 'asset-1', parameters: { confidence: 0.8 }, deployment_id: 'deploy-1', node_id: 'dell', gpu_device_ids: ['0'] })
    const wrapper = shallowMount(WorkspaceView, { global: { stubs } })
    await flushPromises()
    await wrapper.find('button.run-button').trigger('click')
    expect(calls.create).toHaveBeenCalledWith(expect.objectContaining({
      deployment_id: 'deploy-1', parameters: { confidence: 0.8, iou_threshold: 0.7 }, node_id: undefined, gpu_device_ids: [],
    }))
    wrapper.unmount()
  })

  it('resets parameters to the selected service defaults', async () => {
    calls.query = { algorithm: 'algorithm-1', deployment: 'deploy-1' }
    calls.parameters = { confidence: { type: 'number', default: 0.5, minimum: 0, maximum: 1 } }
    calls.deployments.mockResolvedValue([{
      id: 'deploy-1', algorithm_version_id: 'algorithm-1', node_id: 'local', gpu_device_ids: [], status: 'running',
      runtime_profile: { defaults: { confidence: 0.6 }, allowed_overrides: [] },
    }])
    const wrapper = shallowMount(WorkspaceView, { global: { stubs: { ...stubs, ParameterForm: false } } })
    await flushPromises()
    await wrapper.find('input[type="number"]').setValue('0.9')
    await wrapper.findAll('button').find((button) => button.text() === '恢复默认')!.trigger('click')
    await wrapper.find('button.run-button').trigger('click')
    expect(calls.create).toHaveBeenCalledWith(expect.objectContaining({
      deployment_id: 'deploy-1', parameters: { confidence: 0.6 },
    }))
    wrapper.unmount()
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

  it('blocks dirty expert configuration and resets the draft with defaults', async () => {
    calls.parameters = { confidence: { type: 'number', default: 0.5, minimum: 0, maximum: 1 } }
    const wrapper = shallowMount(WorkspaceView, { global: { stubs: { ...stubs, ParameterForm: false } } })
    await flushPromises()
    await wrapper.findAll('button').find((button) => button.text() === 'YAML / JSON')!.trigger('click')
    await wrapper.find('textarea[aria-label="专家参数配置"]').setValue('confidence: 0.8')
    expect(wrapper.find('button.run-button').attributes('disabled')).toBeDefined()
    await wrapper.findAll('button').find((button) => button.text() === '恢复默认')!.trigger('click')
    expect(wrapper.find('button.run-button').attributes('disabled')).toBeUndefined()
    await wrapper.find('button.run-button').trigger('click')
    expect(calls.create).toHaveBeenCalledWith(expect.objectContaining({ parameters: { confidence: 0.5 } }))
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

  it('submits service_key when a service route is selected', async () => {
    calls.query = { algorithm: 'algorithm-1', service: 'helmet-prod' }
    calls.deployments.mockResolvedValue([
      { id: 'deploy-1', algorithm_version_id: 'algorithm-1', node_id: 'dell', gpu_device_ids: ['0'], status: 'running', runtime_profile: { defaults: { confidence: 0.65 }, allowed_overrides: ['confidence'], cpu_count: 1, memory_mb: null, shm_mb: 64, prediction_timeout_seconds: 120, threads: 1, warmup_runs: 1 } },
    ])
    calls.routes.mockResolvedValue([
      { id: 'r1', key: 'helmet-prod', name: '安全帽线上服务', active_deployment_id: 'deploy-1', active_algorithm_version_id: 'algorithm-1', previous_deployment_id: null, previous_algorithm_version_id: null, candidate_deployment_id: null, candidate_algorithm_version_id: null, rollout_status: 'active', drain_timeout_seconds: 120, last_error: null, updated_by: 'admin', created_at: '2026-10-23T00:00:00Z', updated_at: '2026-10-23T00:00:00Z' },
    ])
    calls.parameters = { confidence: { type: 'number', default: 0.25, minimum: 0, maximum: 1 } }
    const wrapper = shallowMount(WorkspaceView, { global: { stubs } })
    await flushPromises()
    expect(wrapper.find('.placement-section summary').text()).toContain('服务入口 · 安全帽线上服务（helmet-prod）')
    await wrapper.find('button.run-button').trigger('click')
    await flushPromises()
    expect(calls.create).toHaveBeenCalledWith({
      algorithm_version_id: 'algorithm-1',
      asset_id: 'asset-1',
      parameters: { confidence: 0.65 },
      node_id: undefined,
      gpu_device_ids: [],
      service_key: 'helmet-prod',
    })
  })
})
