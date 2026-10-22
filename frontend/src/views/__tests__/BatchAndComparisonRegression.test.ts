import { flushPromises, mount, shallowMount } from '@vue/test-utils'
import { reactive, ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const state = vi.hoisted(() => ({
  route: { params: {} as Record<string, string>, query: {} as Record<string, string> },
  createTask: vi.fn(), getComparison: vi.fn(), createComparison: vi.fn(), push: vi.fn(),
}))
vi.mock('vue-router', () => ({ useRoute: () => reactive(state.route), useRouter: () => ({ push: state.push }) }))
vi.mock('@/api/tasks', () => ({ tasksApi: {
  get: vi.fn(), create: state.createTask, subscribe: vi.fn(), byIds: vi.fn().mockResolvedValue([]),
  page: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  result: vi.fn().mockResolvedValue({ result: {
    type: 'object_detection', data: { detections: [] }, input: { width: 10, height: 10 }, timing: { inference_ms: 1 },
  } }),
} }))
vi.mock('@/api/comparisons', () => ({ comparisonsApi: { get: state.getComparison, create: state.createComparison, list: vi.fn().mockResolvedValue([]) } }))
vi.mock('@/api/system', () => ({ systemApi: { executionResources: vi.fn().mockResolvedValue({
  nodes: [{ id: 'dell', name: 'Dell' }, { id: 'local', name: 'Local' }],
  gpus: [{ node_id: 'dell', index: 0, name: 'GPU', memory_total_mb: 100, memory_used_mb: 0 }, { node_id: 'local', index: 0, name: 'GPU', memory_total_mb: 100, memory_used_mb: 0 }],
}) } }))
vi.mock('@/api/assets', () => ({ assetsApi: { thumbnailUrl: () => '', contentUrl: () => '', byIds: vi.fn().mockResolvedValue([]) } }))
vi.mock('@/composables/useAlgorithms', () => ({ useAlgorithms: () => {
  const algorithms = ref(['a', 'b'].map((id) => ({ id, name: id, version: '1', status: 'available', device: 'gpu', task_type: 'object_detection',
    parameters: { confidence: { type: 'number', title: '置信度', minimum: 0, maximum: 1, default: 0.4 } },
  })))
  return { algorithms, availableAlgorithms: algorithms, loading: ref(false), error: ref('') }
} }))
vi.mock('@/composables/useAssets', () => ({ useAssets: () => ({
  assets: ref(['i1', 'i2'].map((id) => ({ id, original_name: `${id}.png`, width: 10, height: 10 }))),
  uploading: ref(false), uploadProgress: ref(null), error: ref(''), upload: vi.fn(),
}) }))
vi.mock('@/composables/useAssetPages', () => ({ useAssetPages: () => ({
  items: ref(['i1', 'i2'].map((id) => ({ id, original_name: `${id}.png`, width: 10, height: 10 }))),
  total: ref(2), query: ref(''), loading: ref(false), error: ref(''), hasMore: ref(false),
  refresh: vi.fn(), loadMore: vi.fn(), prepend: vi.fn(), remove: vi.fn(),
}) }))

import WorkspaceView from '../WorkspaceView.vue'
import CompareView from '../CompareView.vue'
import ParameterForm from '@/components/algorithms/ParameterForm.vue'
import ImageFilmstrip from '@/components/workbench/ImageFilmstrip.vue'

const stubs = { RouterLink: true }
beforeEach(() => {
  vi.clearAllMocks()
  state.route.params = {}; state.route.query = {}
  state.createTask.mockReset().mockResolvedValue({ id: 'new-task' })
  state.createComparison.mockResolvedValue({ id: 'new-comparison', tasks: [] })
  state.push.mockImplementation(async (target) => {
    if (typeof target === 'string') { state.route.params = {}; state.route.query = {} }
    else if (target.path === '/compare') {
      state.route.params = {}; state.route.query = target.query
      window.history.replaceState({ ...window.history.state, ...(target.state ?? {}) }, '')
    }
  })
})

describe('batch and comparison regressions', () => {
  it('keeps the frozen parameters and placement for every task in a batch', async () => {
    let release!: (value: unknown) => void
    state.createTask.mockImplementationOnce(() => new Promise((resolve) => { release = resolve }))
    const wrapper = shallowMount(WorkspaceView, { global: { stubs } })
    await flushPromises()
    wrapper.findComponent(ImageFilmstrip).vm.$emit('update:selected', ['i1', 'i2'])
    await wrapper.find('fieldset select').setValue('dell')
    await wrapper.find('.run-button').trigger('click'); await flushPromises()
    wrapper.findComponent(ParameterForm).vm.$emit('update:modelValue', { confidence: 0.9 })
    await wrapper.find('fieldset select').setValue('local')
    release({ id: 'first-task' }); await flushPromises()
    expect(state.createTask.mock.calls[1]![0]).toMatchObject({ parameters: { confidence: 0.4 }, node_id: 'dell' })
  })

  it('keeps only unsent images after a partial batch failure', async () => {
    state.createTask.mockResolvedValueOnce({ id: 'first-task' }).mockRejectedValueOnce(new Error('temporary failure'))
    const wrapper = shallowMount(WorkspaceView, { global: { stubs } })
    await flushPromises()
    wrapper.findComponent(ImageFilmstrip).vm.$emit('update:selected', ['i1', 'i2']); await flushPromises()
    await wrapper.find('.run-button').trigger('click'); await flushPromises()
    await wrapper.find('.run-button').trigger('click'); await flushPromises()
    expect(state.createTask.mock.calls.map(([payload]) => payload.asset_id)).toEqual(['i1', 'i2', 'i2'])
  })

  it('restores original comparison parameters when rerunning with the same settings', async () => {
    state.route.params = { id: 'original' }
    state.getComparison.mockResolvedValue({ id: 'original', asset_id: 'i1', created_at: '', tasks: ['a', 'b'].map((id) => ({
      id: `task-${id}`, algorithm_version_id: id, status: 'completed', parameters: { confidence: 0.85 },
    })) })
    const wrapper = mount(CompareView, { global: { stubs } })
    await flushPromises()
    const rerunButton = wrapper.findAll('button').find((button) => button.text().includes('用相同设置再建一次'))
    expect(rerunButton).toBeDefined()
    await rerunButton!.trigger('click'); await flushPromises()
    expect(state.push).toHaveBeenCalledWith(expect.objectContaining({
      path: '/compare',
      state: { cvComparisonParameters: { a: { confidence: 0.85 }, b: { confidence: 0.85 } } },
    }))
    wrapper.unmount()

    state.route.params = {}; state.route.query = { asset: 'i1', algorithms: 'a,b' }
    const builder = mount(CompareView, { global: { stubs } })
    await flushPromises()
    await builder.find('.compare-submit button').trigger('click'); await flushPromises()
    expect(state.createComparison).toHaveBeenCalledWith(expect.objectContaining({ parameters: { a: { confidence: 0.85 }, b: { confidence: 0.85 } } }))
    builder.unmount()
  })
})
