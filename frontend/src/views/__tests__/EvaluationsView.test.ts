import { flushPromises, mount, shallowMount } from '@vue/test-utils'
import { ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const calls = vi.hoisted(() => ({
  datasets: vi.fn(), runs: vi.fn(), createDataset: vi.fn(), start: vi.fn(), report: vi.fn(), upload: vi.fn(), push: vi.fn(),
}))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: calls.push }), useRoute: () => ({ params: { id: 'run-1' } }) }))
vi.mock('@/api/evaluations', () => ({ evaluationsApi: {
  datasets: calls.datasets, runs: calls.runs, createDataset: calls.createDataset, start: calls.start, report: calls.report, deleteDataset: vi.fn(),
} }))
vi.mock('@/api/assets', () => ({ assetsApi: { thumbnailUrl: () => '' } }))
vi.mock('@/composables/useAssets', () => ({ uploadInBatches: calls.upload, uploadFailureMessage: () => '' }))
vi.mock('@/composables/useAlgorithms', () => ({ useAlgorithms: () => {
  const algorithms = ref([
    { id: 'det-a', name: '检测 A', version: '1', task_type: 'object_detection', status: 'available' },
    { id: 'det-b', name: '检测 B', version: '2', task_type: 'object_detection', status: 'available' },
    { id: 'cls', name: '分类', version: '1', task_type: 'classification', status: 'available' },
  ])
  return { algorithms, availableAlgorithms: algorithms }
} }))

import UploadDropzone from '@/components/assets/UploadDropzone.vue'
import TaskDrawer from '@/components/tasks/TaskDrawer.vue'
import EvaluationReportView from '../EvaluationReportView.vue'
import EvaluationsView from '../EvaluationsView.vue'

const stubs = { RouterLink: true }
const dataset = { id: 'ds-1', name: '街景', task_type: 'object_detection', item_count: 12, created_by: 'admin', created_at: '2026-10-22T00:00:00Z' }

describe('dataset evaluation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    calls.datasets.mockResolvedValue([dataset])
    calls.runs.mockResolvedValue([])
  })

  it('uploads images, sends the annotation file and reports unmatched names', async () => {
    calls.upload.mockImplementation(async (files, options) => {
      // 内容去重后服务端保留首次上传时的名称；标注应匹配此次上传的文件名。
      const uploaded = [{ id: 'img-1', original_name: 'old-a.png' }, { id: 'img-2', original_name: 'old-b.png' }]
      options.onUploaded(uploaded, files)
      return { uploaded, failed: [] }
    })
    calls.createDataset.mockResolvedValue({ ...dataset, item_count: 1, unmatched_files: ['c.png'], unlabeled_images: ['b.png'] })
    const wrapper = mount(EvaluationsView, { global: { stubs } })
    await flushPromises()
    await wrapper.findAll('button').find((button) => button.text() === '新建评测集')!.trigger('click')
    await wrapper.find('input[maxlength="120"]').setValue('夜间行人')
    wrapper.findComponent(UploadDropzone).vm.$emit('files', [new File(['x'], 'a.png'), new File(['y'], 'b.png')])
    await flushPromises()
    const input = wrapper.find('input[type="file"][aria-label="选择标注文件"]')
    const annotation = new File(['{"images": [], "annotations": []}'], 'coco.json', { type: 'application/json' })
    // jsdom 的 File 没有实现 text()，浏览器里有
    Object.defineProperty(annotation, 'text', { value: async () => '{"images": [], "annotations": []}' })
    Object.defineProperty(input.element, 'files', { value: [annotation] })
    await input.trigger('change')
    await wrapper.findAll('button').find((button) => button.text() === '创建评测集')!.trigger('click')
    await flushPromises()
    expect(calls.createDataset).toHaveBeenCalledWith({
      name: '夜间行人', task_type: 'object_detection', annotations_filename: 'coco.json',
      annotations: '{"images": [], "annotations": []}', asset_ids: ['img-1', 'img-2'],
      asset_names: { 'img-1': 'a.png', 'img-2': 'b.png' },
    })
    expect(wrapper.text()).toContain('c.png')
    expect(wrapper.text()).toContain('1 张图片没有标注')
  })

  it('only offers algorithms of the dataset task type and opens the report after starting', async () => {
    calls.start.mockResolvedValue({ id: 'run-9', task_count: 24 })
    const wrapper = shallowMount(EvaluationsView, { global: { stubs } })
    await flushPromises()
    await wrapper.findAll('button').find((button) => button.text() === '运行评测')!.trigger('click')
    const offered = wrapper.findAll('.evaluation-algorithm').map((item) => item.text())
    expect(offered).toEqual(['检测 A v1', '检测 B v2'])
    expect(wrapper.text()).toContain('共 24 个任务')
    await wrapper.findAll('button').find((button) => button.text() === '开始评测')!.trigger('click')
    await flushPromises()
    expect(calls.start).toHaveBeenCalledWith({ dataset_id: 'ds-1', algorithm_version_ids: ['det-a', 'det-b'] })
    expect(calls.push).toHaveBeenCalledWith('/evaluations/run-9')
  })

  it('highlights the best algorithm per metric and opens the worst samples', async () => {
    calls.report.mockResolvedValue({
      run: { id: 'run-1', dataset_name: '街景', task_type: 'object_detection', task_count: 4, finished: true, created_at: '2026-10-22T00:00:00Z' },
      report: {
        task_type: 'object_detection', primary_metric: 'f1_iou50', finished: true,
        algorithms: [
          { algorithm_version_id: 'det-a', total: 2, completed: 2, failed: 0, active: 0, metrics: { f1_iou50: 0.9, false_positive: 3 }, worst: [{ task_id: 't1', asset_id: 'a1', value: 0.5 }] },
          { algorithm_version_id: 'det-b', total: 2, completed: 2, failed: 0, active: 0, metrics: { f1_iou50: 0.7, false_positive: 1 }, worst: [] },
        ],
      },
    })
    const wrapper = shallowMount(EvaluationReportView, { global: { stubs } })
    await flushPromises()
    const rows = wrapper.findAll('tbody tr')
    expect(rows[0]!.find('td.best').text()).toBe('90.0%')
    expect(rows[1]!.find('td.best').text()).toBe('1')
    await wrapper.find('.worst-sample').trigger('click')
    expect(wrapper.findComponent(TaskDrawer).props('taskId')).toBe('t1')
  })
})
