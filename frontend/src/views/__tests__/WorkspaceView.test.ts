import { flushPromises, shallowMount } from '@vue/test-utils'
import { ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const calls = vi.hoisted(() => ({ create: vi.fn(), resources: vi.fn(), push: vi.fn(), getTask: vi.fn(), query: {} as Record<string, string>, parameters: {} as Record<string, unknown> }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: calls.push }), useRoute: () => ({ query: calls.query }) }))
vi.mock('@/api/tasks', () => ({ tasksApi: { get: calls.getTask } }))
vi.mock('@/api/system', () => ({ systemApi: { executionResources: calls.resources } }))
vi.mock('@/composables/useAlgorithms', () => ({ useAlgorithms: () => {
  const algorithms = ref([{ id: 'algorithm-1', name: 'Detector', device: 'gpu', parameters: calls.parameters, status: 'available' }])
  return { algorithms, availableAlgorithms: algorithms, loading: ref(false), error: ref('') }
} }))
vi.mock('@/composables/useAssets', () => ({ useAssets: () => ({
  assets: ref([{ id: 'asset-1', original_name: 'image.jpg', width: 10, height: 10 }]),
  uploading: ref(false), error: ref(''), upload: vi.fn(),
}) }))
vi.mock('@/composables/useTasks', () => ({ useTasks: () => ({
  tasks: ref([]), submitting: ref(false), error: ref(''), create: calls.create,
}) }))

import WorkspaceView from '../WorkspaceView.vue'

describe('task execution placement', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    calls.query = {}
    calls.parameters = {}
    calls.create.mockResolvedValue({ id: 'task-1' })
    calls.resources.mockResolvedValue({
      nodes: [{ id: 'local', name: 'Platform server' }, { id: 'dell', name: 'Dell GPU' }],
      gpus: [{ node_id: 'dell', node_name: 'Dell GPU', index: 0, name: 'RTX 5070 Ti', memory_total_mb: 16303, memory_used_mb: 18 }],
    })
  })

  it('submits the selected node and card', async () => {
    const wrapper = shallowMount(WorkspaceView, { global: { stubs: { RouterLink: true } } })
    await flushPromises()
    await wrapper.find('fieldset select').setValue('dell')
    expect(wrapper.find('fieldset').text()).toContain('RTX 5070 Ti')
    await wrapper.findAll('fieldset select')[1]!.setValue('0')
    await wrapper.find('button.primary-button').trigger('click')
    await flushPromises()
    expect(calls.create).toHaveBeenCalledWith({ algorithm_version_id: 'algorithm-1', asset_id: 'asset-1', parameters: {}, node_id: 'dell', gpu_device_ids: ['0'] })
    expect(calls.push).toHaveBeenCalledWith('/tasks/task-1')
  })

  it('clears a card when switching back to automatic scheduling', async () => {
    const wrapper = shallowMount(WorkspaceView, { global: { stubs: { RouterLink: true } } })
    await flushPromises()
    await wrapper.find('fieldset select').setValue('dell')
    await wrapper.findAll('fieldset select')[1]!.setValue('0')
    await wrapper.find('fieldset select').setValue('')
    await wrapper.find('button.primary-button').trigger('click')
    expect(calls.create).toHaveBeenCalledWith(expect.objectContaining({ node_id: undefined, gpu_device_ids: [] }))
  })

  it('restores the original task node and GPU before rerunning', async () => {
    calls.query = { from_task: 'original-task' }
    calls.getTask.mockResolvedValue({ algorithm_version_id: 'algorithm-1', asset_id: 'asset-1', parameters: {}, node_id: 'dell', gpu_device_ids: ['0'] })
    const wrapper = shallowMount(WorkspaceView, { global: { stubs: { RouterLink: true } } })
    await flushPromises()
    expect((wrapper.find('fieldset select').element as HTMLSelectElement).value).toBe('dell')
    expect((wrapper.findAll('fieldset select')[1]!.element as HTMLSelectElement).value).toBe('0')
    await wrapper.find('button.primary-button').trigger('click')
    expect(calls.create).toHaveBeenCalledWith(expect.objectContaining({ node_id: 'dell', gpu_device_ids: ['0'] }))
  })

  it('blocks invalid numeric parameters before submitting', async () => {
    calls.parameters = { confidence: { type: 'number', title: '置信度', default: 0.5, minimum: 0, maximum: 1 } }
    const wrapper = shallowMount(WorkspaceView, { global: { stubs: { RouterLink: true } } })
    await flushPromises()
    await wrapper.find('input[type="number"]').setValue('2')
    expect(wrapper.text()).toContain('置信度不能大于 1')
    expect(wrapper.find('button.primary-button').attributes('disabled')).toBeDefined()
    expect(calls.create).not.toHaveBeenCalled()
  })

  it('keeps input and placement when submission fails', async () => {
    calls.create.mockRejectedValue(new Error('network unavailable'))
    const wrapper = shallowMount(WorkspaceView, { global: { stubs: { RouterLink: true } } })
    await flushPromises()
    await wrapper.find('fieldset select').setValue('dell')
    await wrapper.find('button.primary-button').trigger('click')
    await flushPromises()
    expect(calls.push).not.toHaveBeenCalled()
    expect((wrapper.find('fieldset select').element as HTMLSelectElement).value).toBe('dell')
    expect(wrapper.text()).toContain('image.jpg')
  })
})
