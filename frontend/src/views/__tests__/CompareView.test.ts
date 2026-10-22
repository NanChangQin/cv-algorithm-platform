import { flushPromises, mount } from '@vue/test-utils'
import { reactive, ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const calls = vi.hoisted(() => ({
  create: vi.fn(), list: vi.fn(), push: vi.fn(), byIds: vi.fn(), upload: vi.fn(), algorithms: [] as unknown[],
  images: {
    'asset-1': { id: 'asset-1', original_name: 'bear.jpg', width: 10, height: 10 },
    'asset-9': { id: 'asset-9', original_name: 'old.jpg', width: 10, height: 10 },
  } as Record<string, { id: string; original_name: string; width: number; height: number }>,
}))
const route = vi.hoisted(() => ({ params: {} as Record<string, string>, query: {} as Record<string, string> }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: calls.push }), useRoute: () => reactive(route) }))
vi.mock('@/api/comparisons', () => ({ comparisonsApi: { create: calls.create, list: calls.list, get: vi.fn() } }))
vi.mock('@/api/tasks', () => ({ tasksApi: { result: vi.fn() } }))
// 第一页只有最新的 bear.jpg；old.jpg 不在第一页，只能按 id 读取
vi.mock('@/api/assets', () => ({ assetsApi: {
  thumbnailUrl: () => '', contentUrl: () => '', byIds: calls.byIds,
  page: vi.fn(async () => ({ items: [calls.images['asset-1']], total: 1 })),
} }))
vi.mock('@/composables/useAlgorithms', () => ({ useAlgorithms: () => {
  const algorithms = ref(calls.algorithms)
  return { algorithms, availableAlgorithms: algorithms, loading: ref(false) }
} }))
vi.mock('@/composables/useAssets', () => ({ uploadInBatches: calls.upload }))

import UploadDropzone from '@/components/assets/UploadDropzone.vue'
import CompareView from '../CompareView.vue'

const detector = {
  id: 'detector', name: 'Detector', version: '1.0.0', task_type: 'object_detection', device: 'gpu', status: 'available',
  parameters: { confidence: { type: 'number', default: 0.4, minimum: 0, maximum: 1, title: '置信度' } },
}
const classifier = { id: 'classifier', name: 'Classifier', version: '2.0.0', task_type: 'classification', device: 'cpu', status: 'available', parameters: {} }
const stubs = { RouterLink: { template: '<a><slot /></a>' }, UploadDropzone: true }

describe('comparison builder', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    route.params = {}
    route.query = {}
    calls.list.mockResolvedValue([])
    calls.byIds.mockImplementation(async (ids: string[]) => ids.map((id) => calls.images[id]).filter(Boolean))
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

  it('selects a linked image that is not on the first page instead of the newest one', async () => {
    calls.algorithms = [detector, classifier]
    route.query = { asset: 'asset-9', algorithms: 'detector,classifier' }
    const wrapper = mount(CompareView, { global: { stubs } })
    await flushPromises()
    expect(calls.byIds).toHaveBeenCalledWith(['asset-9'])
    expect(wrapper.find('.compare-submit').text()).toContain('将用 2 个算法分析 old.jpg')
    expect(wrapper.find('.asset-option.selected').text()).toContain('old.jpg')
  })

  it('selects an uploaded image and shows it first', async () => {
    calls.algorithms = [detector, classifier]
    const uploaded = { id: 'asset-new', original_name: 'new.png', width: 10, height: 10 }
    calls.upload.mockResolvedValue({ uploaded: [uploaded], failed: [] })
    const wrapper = mount(CompareView, { global: { stubs } })
    await flushPromises()
    wrapper.findComponent(UploadDropzone).vm.$emit('files', [new File(['x'], 'new.png', { type: 'image/png' })])
    await flushPromises()
    expect(wrapper.findAll('.asset-option small').map((node) => node.text())).toEqual(['new.png', 'bear.jpg'])
    expect(wrapper.find('.asset-option.selected').text()).toContain('new.png')
  })

  it('names the images of recent comparisons with a single lookup', async () => {
    calls.algorithms = [detector, classifier]
    calls.list.mockResolvedValue(['c1', 'c2'].map((id) => ({ id, asset_id: 'asset-9', tasks: [], created_at: '2026-10-22T08:00:00Z' })))
    const wrapper = mount(CompareView, { global: { stubs } })
    await flushPromises()
    expect(calls.byIds.mock.calls).toEqual([[['asset-9']]])
    expect(wrapper.findAll('.recent-comparison').map((item) => item.text())).toEqual([
      expect.stringContaining('old.jpg'), expect.stringContaining('old.jpg'),
    ])
  })
})
