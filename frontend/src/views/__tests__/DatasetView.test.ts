import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defaultSplitConfig } from '@/api/training'
import DatasetSplitForm from '@/components/training/DatasetSplitForm.vue'
import DatasetView from '../DatasetView.vue'

const calls = vi.hoisted(() => ({
  dataset: vi.fn(), datasets: vi.fn(), images: vi.fn(), create: vi.fn(), push: vi.fn(),
}))
vi.mock('vue-router', () => ({ useRoute: () => ({ params: { id: 'ds-1' } }), useRouter: () => ({ push: calls.push }) }))
vi.mock('@/api/training', async (original) => ({
  ...await original<typeof import('@/api/training')>(),
  trainingApi: {
    dataset: calls.dataset, datasets: calls.datasets, datasetImages: calls.images,
    newDatasetVersion: calls.create,
    datasetPreviewUrl: (id: string, image: number) => `/datasets/${id}/${image}.jpg`,
    datasetArchiveUrl: (id: string, source = false) => `/datasets/${id}.zip?source=${source}`,
    datasetReportUrl: (id: string) => `/datasets/${id}/report`,
  },
}))
const stats = { images: 2, labeled: 1, backgrounds: 1, boxes: 1 }
const dataset = {
  id: 'ds-1', family_id: 'ds-1', parent_id: null, name: '道路检测', version: 1,
  class_names: ['car'], sha256: 'normalized-hash', source_sha256: 'source-hash',
  split_config: defaultSplitConfig, size_bytes: 1024, created_by: 'admin', created_at: '2026-10-22T00:00:00Z',
  stats: { splits: { train: stats, val: stats, test: stats }, instances: { car: 3 },
    split_instances: { train: { 0: 1 }, val: { 0: 1 }, test: { 0: 1 } },
    warnings: ['存在跨划分重复图片'], invalid_examples: ['labels/a.txt 第 2 行错误'], invalid_lines: 1,
    duplicate_groups: 1, duplicate_images: 2, leakage_groups: 1 },
}
const image = { id: 0, path: 'images/train/a.jpg', source_path: 'train/a.jpg', split: 'train',
  width: 400, height: 200, sha256: 'image-hash', boxes: [{ class_id: 0, cx: .5, cy: .5, width: .2, height: .4 }] }
const stubs = { RouterLink: { props: ['to'], template: '<a><slot /></a>' } }

describe('dataset lifecycle', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    calls.dataset.mockResolvedValue(dataset)
    calls.datasets.mockResolvedValue([dataset, { ...dataset, id: 'ds-2', version: 2 }])
    calls.images.mockResolvedValue({ total: 15, items: [image] })
  })

  it('shows immutable provenance, three splits and annotation rectangles', async () => {
    const wrapper = mount(DatasetView, { global: { stubs } })
    await flushPromises()
    expect(wrapper.text()).toContain('normalized-hash')
    expect(wrapper.text()).toContain('source-hash')
    expect(wrapper.text()).toContain('跨划分泄漏组 1')
    expect(wrapper.findAll('.dataset-table tbody tr')).toHaveLength(4)
    expect(wrapper.find('svg rect').attributes()).toMatchObject({ x: '160', y: '60', width: '80', height: '80' })
    expect(wrapper.find('svg image').attributes('href')).toBe('/datasets/ds-1/0.jpg')
    await wrapper.find('input[type="checkbox"]').setValue(false)
    expect(wrapper.find('svg rect').exists()).toBe(false)
    expect(wrapper.findAll('select')[0]!.findAll('option')).toHaveLength(2)
    await wrapper.findAll('select')[0]!.setValue('ds-2')
    expect(calls.push).toHaveBeenCalledWith('/training/datasets/ds-2')
  })

  it('filters and pages the preview without leaking a previous filter', async () => {
    const wrapper = mount(DatasetView, { global: { stubs } })
    await flushPromises()
    await wrapper.find('select[aria-label="预览划分"]').setValue('test')
    await flushPromises()
    expect(calls.images).toHaveBeenLastCalledWith('ds-1', 'test', 0)
    await wrapper.findAll('button').find((b) => b.text() === '下一页')!.trigger('click')
    await flushPromises()
    expect(calls.images).toHaveBeenLastCalledWith('ds-1', 'test', 12)
    await wrapper.find('select[aria-label="预览划分"]').setValue('val')
    await flushPromises()
    expect(calls.images).toHaveBeenLastCalledWith('ds-1', 'val', 0)
  })

  it('creates a new version from explicit ratios and seed', async () => {
    calls.create.mockResolvedValue({ id: 'ds-new', version: 3 })
    const wrapper = mount(DatasetView, { global: { stubs } })
    await flushPromises()
    const form = wrapper.findComponent(DatasetSplitForm)
    await form.find('input[aria-label="train 比例"]').setValue(60)
    await form.find('input[aria-label="test 比例"]').setValue(20)
    await form.find('input[aria-label="划分随机种子"]').setValue(123)
    await wrapper.findAll('button').find((b) => b.text() === '生成新版本')!.trigger('click')
    await flushPromises()
    expect(calls.create).toHaveBeenCalledWith('ds-1', { ...defaultSplitConfig, mode: 'reshuffle', train: .6, test: .2, seed: 123 })
    expect(calls.push).toHaveBeenCalledWith('/training/datasets/ds-new')
  })

  it('blocks invalid ratios and explains the missing original of historical data', async () => {
    const wrapper = mount(DatasetView, { global: { stubs } })
    await flushPromises()
    await wrapper.find('input[aria-label="train 比例"]').setValue(90)
    expect(wrapper.text()).toContain('合计 100%')
    expect(wrapper.findAll('button').find((b) => b.text() === '生成新版本')!.attributes('disabled')).toBeDefined()
    wrapper.unmount()
    calls.dataset.mockResolvedValue({ ...dataset, source_sha256: null, split_config: {} })
    const legacy = mount(DatasetView, { global: { stubs } })
    await flushPromises()
    expect(legacy.text()).toContain('历史版本未保留原包')
    expect(legacy.findComponent(DatasetSplitForm).exists()).toBe(false)
  })
})
