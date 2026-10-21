import { flushPromises, mount } from '@vue/test-utils'
import { reactive, ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const calls = vi.hoisted(() => ({ create: vi.fn(), list: vi.fn(), push: vi.fn(), algorithms: [] as unknown[] }))
const route = vi.hoisted(() => ({ params: {} as Record<string, string>, query: {} as Record<string, string> }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: calls.push }), useRoute: () => reactive(route) }))
vi.mock('@/api/comparisons', () => ({ comparisonsApi: { create: calls.create, list: calls.list, get: vi.fn() } }))
vi.mock('@/api/tasks', () => ({ tasksApi: { result: vi.fn() } }))
vi.mock('@/api/assets', () => ({ assetsApi: { thumbnailUrl: () => '', contentUrl: () => '' } }))
vi.mock('@/composables/useAlgorithms', () => ({ useAlgorithms: () => {
  const algorithms = ref(calls.algorithms)
  return { algorithms, availableAlgorithms: algorithms, loading: ref(false) }
} }))
vi.mock('@/composables/useAssets', () => ({ useAssets: () => ({
  assets: ref([{ id: 'asset-1', original_name: 'bear.jpg', width: 10, height: 10 }]),
  uploading: ref(false), uploadProgress: ref(null), upload: vi.fn(),
}) }))

import CompareView from '../CompareView.vue'

const detector = {
  id: 'detector', name: 'Detector', version: '1.0.0', task_type: 'object_detection', device: 'gpu', status: 'available',
  parameters: { confidence: { type: 'number', default: 0.4, minimum: 0, maximum: 1, title: '置信度' } },
}
const classifier = { id: 'classifier', name: 'Classifier', version: '2.0.0', task_type: 'classification', device: 'cpu', status: 'available', parameters: {} }
const stubs = { RouterLink: true, UploadDropzone: true }

describe('comparison builder', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    route.params = {}
    route.query = {}
    calls.list.mockResolvedValue([])
    calls.create.mockResolvedValue({ id: 'comparison-1', asset_id: 'asset-1', tasks: [], created_at: '' })
  })

  it('explains why a comparison needs two algorithms', async () => {
    calls.algorithms = [detector]
    const wrapper = mount(CompareView, { global: { stubs } })
    await flushPromises()
    expect(wrapper.text()).toContain('至少需要两个可用算法才能对比')
  })

  it("submits each algorithm's own defaults instead of a fixed confidence", async () => {
    calls.algorithms = [detector, classifier]
    const wrapper = mount(CompareView, { global: { stubs } })
    await flushPromises()
    for (const toggle of wrapper.findAll('.compare-algorithm-toggle')) await toggle.trigger('click')
    await wrapper.find('.compare-submit button').trigger('click')
    await flushPromises()
    expect(calls.create).toHaveBeenCalledWith({
      asset_id: 'asset-1',
      algorithm_version_ids: ['detector', 'classifier'],
      parameters: { detector: { confidence: 0.4 }, classifier: {} },
    })
    expect(calls.push).toHaveBeenCalledWith('/compare/comparison-1')
  })

  it('preselects the image and algorithm passed in the link', async () => {
    calls.algorithms = [detector, classifier]
    route.query = { asset: 'asset-1', algorithms: 'classifier' }
    const wrapper = mount(CompareView, { global: { stubs } })
    await flushPromises()
    expect(wrapper.find('.compare-algorithm.selected').text()).toContain('Classifier')
    expect(wrapper.find('.compare-submit').text()).toContain('至少选择两个算法')
  })
})
