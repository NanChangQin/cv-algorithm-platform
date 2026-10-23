import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const calls = vi.hoisted(() => ({
  list: vi.fn(), get: vi.fn(), create: vi.fn(), logs: vi.fn(), report: vi.fn(), stop: vi.fn(), remove: vi.fn(), publish: vi.fn(),
  datasets: vi.fn(), models: vi.fn(), sources: vi.fn(), contract: vi.fn(), upload: vi.fn(), resources: vi.fn(), push: vi.fn(),
  route: { params: { id: '' }, query: {} as Record<string, string> },
}))
vi.mock('vue-router', () => ({ useRoute: () => calls.route, useRouter: () => ({ push: calls.push }) }))
vi.mock('@/api/exports', () => ({ exportsApi: {
  list: calls.list, get: calls.get, create: calls.create, logs: calls.logs, report: calls.report,
  stop: calls.stop, remove: calls.remove, publish: calls.publish,
  artifactUrl: (id: string, name: string) => `/exports/${id}/${name}`, archiveUrl: (id: string) => `/exports/${id}.zip`,
} }))
vi.mock('@/api/training', async (original) => ({
  ...(await original<typeof import('@/api/training')>()),
  trainingApi: { datasets: calls.datasets, models: calls.models, sources: calls.sources, parameterContract: calls.contract, uploadWeight: calls.upload },
}))
vi.mock('@/api/system', () => ({ systemApi: { executionResources: calls.resources } }))

import exportContract from '../../../../docs/research/export-parameter-contract.json'
import valContract from '../../../../docs/research/validation-parameter-contract.json'
import ParameterForm from '@/components/algorithms/ParameterForm.vue'
import UploadDropzone from '@/components/assets/UploadDropzone.vue'
import ExportsView from '../ExportsView.vue'

const source = { source: { kind: 'algorithm', reference: 'model-1' }, label: '模型 v1' }
const base = { id: 'export-1', name: '导出实验', status: 'completed', model: 'custom.pt', node_id: 'cpu',
  created_at: '2026-10-23T00:00:00Z', progress: { operation: 'export', source: { label: '模型 v1' } }, artifacts: {}, parameters: {} }
let wrapper: ReturnType<typeof mount>
const render = () => { wrapper = mount(ExportsView, { global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } } }); return wrapper }
const select = (label: string) => wrapper.findAll('label').find((item) => item.text().startsWith(label))!.find('select')
beforeEach(() => {
  vi.clearAllMocks()
  calls.route.params.id = ''; calls.route.query = {}
  calls.list.mockResolvedValue([]); calls.get.mockResolvedValue(base); calls.logs.mockResolvedValue([])
  calls.create.mockResolvedValue(base)
  calls.datasets.mockResolvedValue([{ id: 'dataset-1', name: '测试集', version: 2, stats: { splits: { val: { images: 4 }, train: { images: 8 } } } }])
  calls.models.mockResolvedValue([{ key: 'yolo11n.pt', label: 'YOLO11n' }])
  calls.sources.mockResolvedValue([source]); calls.resources.mockResolvedValue({ nodes: [{ id: 'cpu', name: 'CPU 节点' }], gpus: [] })
  calls.contract.mockImplementation(async (mode: string) => mode === 'export' ? exportContract : valContract)
})
afterEach(() => wrapper?.unmount())

describe('ONNX exports', () => {
  it('publishes a verified export and opens its deployment configuration', async () => {
    calls.route.params.id = 'export-1'
    calls.get.mockResolvedValue({ ...base, artifacts: { 'export-report.json': 100 } })
    calls.report.mockResolvedValue({ input_shape: [1,3,64,64], quality_gate: { passed: true, map50_95_drop: 0, max_map_drop: 0.01 }, reference_metrics: {}, onnx_metrics: {}, metric_deltas: {}, benchmark: {} })
    calls.publish.mockResolvedValue({ id: 'onnx-model' })
    render(); await flushPromises()
    const input = wrapper.findAll('label').find((label) => label.text().startsWith('模型 ID'))!.find('input')
    await input.setValue('helmet-onnx')
    await wrapper.findAll('form')[1]!.trigger('submit'); await flushPromises()
    expect(calls.publish).toHaveBeenCalledWith('export-1', { key: 'helmet-onnx', name: '', version: '' })
    expect(calls.push).toHaveBeenCalledWith({ path: '/deployments', query: { algorithm: 'onnx-model' } })
  })

  it('submits a CPU job with independent export and validation configuration', async () => {
    render(); await flushPromises()
    expect(wrapper.text()).toContain('CPU 节点')
    expect(calls.contract.mock.calls.map((args) => args[0])).toEqual(['export', 'val'])
    await wrapper.find('form').trigger('submit'); await flushPromises()
    expect(calls.create).toHaveBeenCalledWith(expect.objectContaining({
      node_id: 'cpu', dataset_id: 'dataset-1', source: { kind: 'builtin' },
      parameters: expect.objectContaining({ format: 'onnx', batch: 1, dynamic: false }),
      verification: expect.objectContaining({ max_map_drop: 0.01, iterations: 5, parameters: expect.objectContaining({ split: 'val' }) }),
    }))
    expect(calls.create.mock.calls[0]![0]).not.toHaveProperty('gpu_device_ids')
    expect(calls.push).toHaveBeenCalledWith('/exports/export-1')
  })
  it('preselects a published model from its lifecycle link', async () => {
    calls.route.query = { model: 'model-1' }
    render(); await flushPromises()
    expect(select('来源权重').element.value).toContain('model-1')
    await wrapper.find('form').trigger('submit'); await flushPromises()
    expect(calls.create.mock.calls[0]![0].source).toEqual(source.source)
  })
  it('uploads and freezes the selected managed weight', async () => {
    calls.upload.mockResolvedValue({ id: 'weight-1' })
    render(); await flushPromises()
    calls.sources.mockResolvedValue([{ source: { kind: 'upload', reference: 'weight-1' }, label: 'custom.pt' }])
    wrapper.findComponent(UploadDropzone).vm.$emit('files', [new File(['weight'], 'custom.pt')]); await flushPromises()
    await wrapper.find('form').trigger('submit'); await flushPromises()
    expect(calls.create.mock.calls[0]![0].source).toEqual({ kind: 'upload', reference: 'weight-1' })
  })
  it('blocks absent validation splits', async () => {
    render(); await flushPromises()
    wrapper.findAllComponents(ParameterForm)[1]!.vm.$emit('update:modelValue', { split: 'test' }); await flushPromises()
    await wrapper.find('form').trigger('submit')
    expect(wrapper.text()).toContain('数据集没有 test 图片')
    expect(calls.create).not.toHaveBeenCalled()
  })
  it('blocks static batches which cannot cover the dataset', async () => {
    render(); await flushPromises()
    wrapper.findAllComponents(ParameterForm)[0]!.vm.$emit('update:modelValue', { format: 'onnx', batch: 3 }); await flushPromises()
    await wrapper.find('form').trigger('submit')
    expect(wrapper.text()).toContain('静态 ONNX 的验证图片数须整除 batch')
    expect(calls.create).not.toHaveBeenCalled()
  })
  it('requires dynamic export for rectangular inputs', async () => {
    render(); await flushPromises()
    const parameters = { format: 'onnx', batch: 1, imgsz: [64, 96], dynamic: false }
    wrapper.findAllComponents(ParameterForm)[0]!.vm.$emit('update:modelValue', parameters); await flushPromises()
    await wrapper.find('form').trigger('submit')
    expect(wrapper.text()).toContain('静态 ONNX 要求正方形尺寸')
    expect(calls.create).not.toHaveBeenCalled()
    wrapper.findAllComponents(ParameterForm)[0]!.vm.$emit('update:modelValue', { ...parameters, dynamic: true }); await flushPromises()
    await wrapper.find('form').trigger('submit'); await flushPromises()
    expect(calls.create).toHaveBeenCalledOnce()
  })
  it('shows a failed quality gate and preserves report/model downloads', async () => {
    calls.route.params.id = 'export-1'
    calls.get.mockResolvedValue({ ...base, status: 'failed', error: '精度下降', artifacts: { 'export-report.json': 123, 'model/model.onnx': 500 } })
    const timing = { mean_ms_per_image: 5, p50_ms_per_image: 4, p95_ms_per_image: 8, images_per_second: 200 }
    calls.report.mockResolvedValue({ format: 'onnx', precision: 'fp32', input_shape: [1, 3, 64, 64], opset: 17, split: 'val',
      quality_gate: { passed: false, map50_95_drop: 0.02, max_map_drop: 0.01 },
      reference_metrics: { map: 0.4 }, onnx_metrics: { map: 0.38 }, metric_deltas: { map: -0.02 },
      benchmark: { reference: timing, onnx: timing }, source_sha256: 'source', artifact_sha256: 'artifact', dataset_sha256: 'data',
    })
    render(); await flushPromises()
    expect(wrapper.text()).toContain('精度检查未通过')
    expect(wrapper.find('a[href="/exports/export-1/model/model.onnx"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('P95')
    expect(wrapper.text()).toContain('不代表服务并发性能')
  })
})
