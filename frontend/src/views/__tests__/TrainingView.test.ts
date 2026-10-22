import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const calls = vi.hoisted(() => ({
  runs: vi.fn(), run: vi.fn(), datasets: vi.fn(), models: vi.fn(), create: vi.fn(), uploadDataset: vi.fn(),
  stop: vi.fn(), logs: vi.fn(), resources: vi.fn(), push: vi.fn(), route: { params: { id: 'run-1' }, query: {} as Record<string, string> },
}))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: calls.push }), useRoute: () => calls.route }))
vi.mock('@/api/system', () => ({ systemApi: { executionResources: calls.resources } }))
vi.mock('@/api/training', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/training')>()),
  trainingApi: {
    runs: calls.runs, run: calls.run, datasets: calls.datasets, models: calls.models, create: calls.create,
    uploadDataset: calls.uploadDataset, stop: calls.stop, logs: calls.logs, remove: vi.fn(), deleteDataset: vi.fn(),
    artifactUrl: (id: string, name: string) => `/files/${id}/${name}`, archiveUrl: (id: string) => `/files/${id}.zip`,
  },
}))

import { defaultParameters, progressRatio, progressText, type TrainingRun } from '@/api/training'
import UploadDropzone from '@/components/assets/UploadDropzone.vue'
import TrainingChart from '@/components/training/TrainingChart.vue'
import TrainingRunView from '../TrainingRunView.vue'
import TrainingView from '../TrainingView.vue'

const stubs = { RouterLink: { template: '<a><slot /></a>' } }
const split = { images: 40, labeled: 38, backgrounds: 2, boxes: 120 }
const dataset = {
  id: 'ds-1', name: '安全帽', format: 'yolo-detect', class_names: ['helmet', 'head'], size_bytes: 2048,
  stats: { splits: { train: split, val: { ...split, images: 10 } }, instances: { helmet: 100, head: 140 }, warnings: [], invalid_lines: 0, invalid_examples: [], orphan_labels: 0, skipped_files: 0, auto_split: false },
  created_by: 'admin', created_at: '2026-10-22T00:00:00Z',
}
const resources = {
  nodes: [{ id: 'local', name: 'Local' }, { id: 'cpu-box', name: 'CPU 服务器' }, { id: 'dell', name: 'Dell 5070 Ti' }],
  gpus: [
    { node_id: 'dell', node_name: 'Dell 5070 Ti', index: 0, name: 'RTX 5070 Ti', memory_total_mb: 16000, memory_used_mb: 2000, utilization_percent: 3 },
  ],
}
const baseRun: TrainingRun = {
  id: 'run-1', name: '安全帽 · yolo26n', dataset_id: 'ds-1', model: 'yolo26n.pt', parameters: { ...defaultParameters, epochs: 10 },
  node_id: 'dell', gpu_device_ids: ['0'], status: 'running', stage: null,
  progress: { phase: 'train', epoch: 3, epochs: 10, batch: 5, batches: 10 },
  best: { epoch: 2, precision: 0.71, recall: 0.62, map50: 0.68, map50_95: 0.41 }, error: null,
  created_by: 'admin', created_at: '2026-10-22T00:00:00Z', updated_at: '2026-10-22T00:00:00Z',
  started_at: '2026-10-22T00:00:00Z', finished_at: null,
  metrics: [
    { epoch: 1, 'train/box_loss': 1.4, 'train/cls_loss': 2.1, 'val/box_loss': 1.5, 'val/cls_loss': 2.3, 'metrics/mAP50-95(B)': 0.2, 'metrics/mAP50(B)': 0.4, 'metrics/precision(B)': 0.5, 'metrics/recall(B)': 0.4 },
    { epoch: 2, 'train/box_loss': 1.2, 'train/cls_loss': 1.6, 'val/box_loss': 1.3, 'val/cls_loss': 1.9, 'metrics/mAP50-95(B)': 0.41, 'metrics/mAP50(B)': 0.68, 'metrics/precision(B)': 0.71, 'metrics/recall(B)': 0.62 },
  ],
  artifacts: {},
}

describe('training center', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    calls.route.query = {}
    calls.runs.mockResolvedValue([])
    calls.datasets.mockResolvedValue([dataset])
    calls.models.mockResolvedValue([
      { key: 'yolo26n.pt', label: 'YOLO26n', description: '最快' },
      { key: 'yolo26s.pt', label: 'YOLO26s', description: '均衡' },
    ])
    calls.resources.mockResolvedValue(resources)
    calls.logs.mockResolvedValue(['Epoch 3/10'])
  })

  it('only offers servers with a GPU and submits the chosen configuration', async () => {
    calls.create.mockResolvedValue({ id: 'run-9' })
    const wrapper = mount(TrainingView, { global: { stubs } })
    await flushPromises()
    await wrapper.findAll('button').find((button) => button.text() === '新建训练')!.trigger('click')
    await flushPromises()
    const servers = wrapper.findAll('select')[1]!.findAll('option').map((option) => option.text())
    expect(servers).toEqual(['Dell 5070 Ti'])
    await wrapper.find('input[value="yolo26s.pt"]').setValue(true)
    await wrapper.findAll('select')[2]!.setValue('0')
    const numbers = wrapper.findAll('.training-form input[type="number"]')
    await numbers[0]!.setValue(30)
    await numbers[2]!.setValue(960)
    await wrapper.findAll('button').find((button) => button.text() === '开始训练')!.trigger('click')
    await flushPromises()
    expect(calls.create).toHaveBeenCalledWith({
      name: '', dataset_id: 'ds-1', model: 'yolo26s.pt', node_id: 'dell', gpu_device_ids: ['0'],
      parameters: { ...defaultParameters, epochs: 30, imgsz: 960 },
    })
    expect(calls.push).toHaveBeenCalledWith('/training/run-9')
  })

  it('blocks image sizes that are not a multiple of 32', async () => {
    const wrapper = mount(TrainingView, { global: { stubs } })
    await flushPromises()
    await wrapper.findAll('button').find((button) => button.text() === '新建训练')!.trigger('click')
    await flushPromises()
    await wrapper.findAll('.training-form input[type="number"]')[2]!.setValue(650)
    expect(wrapper.text()).toContain('图片尺寸必须是 32 的倍数')
    expect(wrapper.findAll('button').find((button) => button.text() === '开始训练')!.attributes('disabled')).toBeDefined()
  })

  it('uploads a dataset zip and lists what the import fixed', async () => {
    calls.uploadDataset.mockResolvedValue({ ...dataset, id: 'ds-2', name: 'coco8', stats: { ...dataset.stats, warnings: ['没有验证集，已自动抽取 1 张图片作为验证集'], invalid_examples: ['labels/a.txt 第 2 行：坐标须为 0~1 之间的归一化数值'] } })
    const wrapper = mount(TrainingView, { global: { stubs } })
    await flushPromises()
    await wrapper.find('.dataset-upload input').setValue('coco8')
    wrapper.findComponent(UploadDropzone).vm.$emit('files', [new File(['zip'], 'export.zip')])
    await flushPromises()
    expect(calls.uploadDataset).toHaveBeenCalledWith(expect.any(File), 'coco8', expect.any(Function))
    expect(wrapper.text()).toContain('已自动抽取 1 张图片作为验证集')
    expect(wrapper.text()).toContain('第 2 行')
  })

  it('shows the most labelled classes first and expands the rest on request', async () => {
    const names = Array.from({ length: 14 }, (_, index) => `c${index}`)
    const instances = Object.fromEntries(names.map((name, index) => [name, index]))
    calls.datasets.mockResolvedValue([{ ...dataset, class_names: names, stats: { ...dataset.stats, instances } }])
    const wrapper = mount(TrainingView, { global: { stubs } })
    await flushPromises()
    const chips = () => wrapper.findAll('.class-chips > span').map((chip) => chip.text())
    expect(chips()).toHaveLength(12)
    expect(chips()[0]).toBe('c13 13')
    await wrapper.find('.class-chips button').trigger('click')
    expect(chips()).toHaveLength(14)
    expect(wrapper.find('.class-chips button').text()).toBe('收起')
  })

  it('opens the form for a dataset linked from elsewhere', async () => {
    calls.route.query = { dataset: 'ds-1' }
    const wrapper = mount(TrainingView, { global: { stubs } })
    await flushPromises()
    expect(wrapper.find('.training-form').exists()).toBe(true)
    expect((wrapper.find('.training-form select').element as HTMLSelectElement).value).toBe('ds-1')
  })
})

describe('training progress', () => {
  it('counts finished batches inside the current epoch', () => {
    expect(progressRatio(baseRun)).toBeCloseTo(0.25)
    expect(progressText(baseRun)).toBe('第 3/10 轮 · 批次 5/10')
    expect(progressText({ ...baseRun, progress: { ...baseRun.progress!, phase: 'val' } })).toBe('第 3/10 轮 · 验证中')
    expect(progressRatio({ ...baseRun, progress: { phase: 'epoch_end', epoch: 4, epochs: 10 } })).toBeCloseTo(0.4)
    expect(progressText({ ...baseRun, stage: '上传数据集 40%' })).toBe('上传数据集 40%')
    expect(progressText({ ...baseRun, progress: null })).toBe('加载数据和模型')
  })
})

describe('training run page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    calls.datasets.mockResolvedValue([dataset])
    calls.resources.mockResolvedValue(resources)
    calls.logs.mockResolvedValue(['Epoch 3/10', 'done'])
  })

  it('charts metrics and only the losses the model reports', async () => {
    calls.run.mockResolvedValue(baseRun)
    const wrapper = mount(TrainingRunView, { global: { stubs } })
    await flushPromises()
    expect(wrapper.find('.stat-tiles').text()).toContain('41.0')
    expect(wrapper.find('.stat-tiles').text()).toContain('第 2 轮')
    const charts = wrapper.findAllComponents(TrainingChart)
    // YOLO26 没有 dfl 损失：只画 box 和 cls
    expect(charts.map((chart) => chart.props('title'))).toEqual(['验证集指标', '定位损失 box', '分类损失 cls'])
    expect(charts[1]!.props('series')).toEqual([
      { label: '训练集', slot: 1, points: [[1, 1.4], [2, 1.2]] },
      { label: '验证集', slot: 2, points: [[1, 1.5], [2, 1.3]] },
    ])
    expect(wrapper.find('.training-logs').text()).toContain('done')
    await wrapper.findAll('button').find((button) => button.text() === '查看数据表')!.trigger('click')
    expect(wrapper.findAll('.data-table tbody tr')).toHaveLength(2)
    expect(wrapper.find('.data-table tbody tr').text()).toContain('20.0')
  })

  it('stops after confirmation and offers downloads once finished', async () => {
    calls.run.mockResolvedValue(baseRun)
    calls.stop.mockResolvedValue({ ...baseRun, status: 'stopping' })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const wrapper = mount(TrainingRunView, { global: { stubs } })
    await flushPromises()
    await wrapper.findAll('button').find((button) => button.text() === '停止训练')!.trigger('click')
    await flushPromises()
    expect(calls.stop).toHaveBeenCalledWith('run-1')
    expect(wrapper.text()).toContain('正在停止…')

    calls.run.mockResolvedValue({
      ...baseRun, status: 'completed', finished_at: '2026-10-22T01:00:00Z',
      artifacts: { 'weights/best.pt': 5_400_000, 'results.csv': 900, 'BoxPR_curve.png': 1000 },
    })
    const finished = mount(TrainingRunView, { global: { stubs } })
    await flushPromises()
    expect(finished.find('a[download][href="/files/run-1.zip"]').exists()).toBe(true)
    expect(finished.find('.artifact-list').text()).toContain('weights/best.pt')
    expect(finished.find('.artifact-list').text()).not.toContain('BoxPR_curve.png')
    expect(finished.find('.training-figures figcaption').text()).toBe('PR 曲线')
    expect(finished.findAll('button').some((button) => button.text() === '停止训练')).toBe(false)
  })

  it('shows why a training failed', async () => {
    calls.run.mockResolvedValue({ ...baseRun, status: 'failed', error: '显存不足，请减小 batch 或图片尺寸后重试', progress: null, metrics: [] })
    const wrapper = mount(TrainingRunView, { global: { stubs } })
    await flushPromises()
    expect(wrapper.find('.alert').text()).toContain('显存不足')
  })
})

describe('training chart', () => {
  it('shows every series at the hovered epoch', async () => {
    const wrapper = mount(TrainingChart, { props: {
      title: '损失', epochs: 3,
      series: [
        { label: '训练集', slot: 1, points: [[1, 1.5], [2, 1.2], [3, 1.0]] },
        { label: '验证集', slot: 2, points: [[1, 1.6], [2, 1.4]] },
      ],
    } })
    const overlay = wrapper.find('rect')
    overlay.element.getBoundingClientRect = () => ({ left: 0, width: 200, top: 0, height: 100, right: 200, bottom: 100, x: 0, y: 0, toJSON: () => ({}) })
    await overlay.trigger('pointermove', { clientX: 100 })
    const tooltip = wrapper.find('.chart-tooltip')
    expect(tooltip.text()).toContain('第 2 轮')
    expect(tooltip.text()).toContain('1.200')
    expect(tooltip.text()).toContain('1.400')
    await overlay.trigger('pointermove', { clientX: 200 })
    expect(wrapper.find('.chart-tooltip').text()).not.toContain('验证集')
    await overlay.trigger('pointerleave')
    expect(wrapper.find('.chart-tooltip').exists()).toBe(false)
  })
})
