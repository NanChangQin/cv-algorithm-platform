import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const calls = vi.hoisted(() => ({
  list: vi.fn(), create: vi.fn(), stop: vi.fn(), start: vi.fn(), redeploy: vi.fn(), remove: vi.fn(), listRoutes: vi.fn(), saveRoute: vi.fn(), rollbackRoute: vi.fn(), removeRoute: vi.fn(), resources: vi.fn(), get: vi.fn(),
  query: {} as Record<string, string>,
}))
vi.mock('vue-router', () => ({ useRoute: () => ({ query: calls.query }) }))
vi.mock('@/api/algorithms', () => ({ algorithmsApi: { get: calls.get } }))
vi.mock('@/api/system', () => ({ systemApi: { executionResources: calls.resources } }))
vi.mock('@/api/deployments', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/deployments')>()),
  deploymentsApi: { list: calls.list, create: calls.create, stop: calls.stop, start: calls.start, redeploy: calls.redeploy, remove: calls.remove, listRoutes: calls.listRoutes, saveRoute: calls.saveRoute, rollbackRoute: calls.rollbackRoute, removeRoute: calls.removeRoute },
}))
vi.mock('@/composables/useAlgorithms', () => ({ useAlgorithms: () => {
  const algorithms = ref([
    { id: 'det', name: '安全帽检测', version: '1.0.1', device: 'gpu', status: 'available' },
    { id: 'ocr', name: '文字识别', version: '2.0.0', device: 'cpu', status: 'available' },
  ])
  return { algorithms, availableAlgorithms: algorithms }
} }))

import DeploymentsView from '../DeploymentsView.vue'

const stubs = { RouterLink: { props: ['to'], template: '<a :data-to="JSON.stringify(to)"><slot /></a>' } }
const steps = (statuses: string[], failed = '') => ['node', 'image', 'weights', 'start'].map((key, index) => ({
  key, label: ['检查服务器', '准备镜像', '准备权重', '启动实例'][index], status: statuses[index], detail: statuses[index] === 'failed' ? failed : null,
}))
const running = { id: 'd1', algorithm_version_id: 'det', node_id: 'dell', gpu_device_ids: ['0'], status: 'running', steps: steps(['done', 'done', 'skipped', 'done']), instance_id: 'c1', error: null, created_by: 'admin', created_at: '2026-10-22T00:00:00Z', updated_at: '2026-10-22T00:00:00Z', started_at: '2026-10-22T00:01:00Z' }
const failed = { ...running, id: 'd2', node_id: 'local', status: 'failed', steps: steps(['done', 'failed', 'pending', 'pending'], '所有服务器上都没有镜像'), error: '所有服务器上都没有镜像' }

describe('deployments page', () => {
  it('submits CPU resources, runtime settings and locked defaults', async () => {
    calls.query = { algorithm: 'ocr' }
    calls.get.mockResolvedValue({ id: 'ocr', framework: 'onnx', lineage: { export_job_id: 'export-1' }, parameters: { confidence: { type: 'number', default: 0.5, minimum: 0, maximum: 1 } } })
    const wrapper = mount(DeploymentsView, { global: { stubs } })
    await flushPromises()
    const input = (label: string) => wrapper.findAll('label').find((item) => item.text().startsWith(label))!.find('input')
    await input('CPU 数量').setValue('2.5')
    await input('内存上限').setValue('2048')
    await input('共享内存').setValue('128')
    await input('推理超时').setValue('19')
    await input('ORT CPU').setValue('3')
    await input('启动预热').setValue('2')
    await wrapper.findAll('label').find((item) => item.text().startsWith('请求参数覆盖'))!.find('select').setValue('selected')
    await wrapper.find('fieldset input[type="checkbox"]').setValue(false)
    await wrapper.findAll('button').find((button) => button.text() === '开始部署')!.trigger('click')
    await flushPromises()
    expect(calls.create).toHaveBeenCalledWith(expect.objectContaining({ runtime_profile: {
      cpu_count: 2.5, memory_mb: 2048, shm_mb: 128, prediction_timeout_seconds: 19,
      threads: 3, warmup_runs: 2, defaults: { confidence: 0.5 }, allowed_overrides: [],
    } }))
    wrapper.unmount()
  })

  beforeEach(() => {
    vi.clearAllMocks()
    calls.get.mockImplementation(async (id: string) => ({ id, framework: 'onnx', parameters: { confidence: { type: 'number', default: 0.5, minimum: 0, maximum: 1 } } }))
    calls.query = {}
    calls.list.mockResolvedValue([running, failed])
    calls.listRoutes.mockResolvedValue([])
    calls.resources.mockResolvedValue({
      nodes: [{ id: 'local', name: 'Local' }, { id: 'dell', name: 'Dell 5070 Ti' }],
      gpus: [{ node_id: 'dell', node_name: 'Dell 5070 Ti', index: 0, name: 'RTX 5070 Ti', memory_total_mb: 16000, memory_used_mb: 1000, utilization_percent: 0 }],
    })
  })

  it('groups deployments of one version and shows each step', async () => {
    const wrapper = mount(DeploymentsView, { global: { stubs } })
    await flushPromises()
    expect(wrapper.findAll('section h2').map((title) => title.text())).toEqual(['安全帽检测 v1.0.1'])
    const [first, second] = wrapper.findAll('.deployment-card')
    expect(first!.text()).toContain('Dell 5070 Ti · GPU 0')
    expect(first!.findAll('.deployment-steps li').map((step) => step.classes()[0])).toEqual(['step-done', 'step-done', 'step-skipped', 'step-done'])
    expect(first!.find('a').attributes('data-to')).toContain('"deployment":"d1"')
    expect(second!.find('.step-failed small').text()).toBe('所有服务器上都没有镜像')
    expect(second!.findAll('button').map((button) => button.text())).toEqual(['启动', '重新部署', '删除'])
  })

  it('deploys the version picked on the algorithm page to the chosen GPU', async () => {
    calls.query = { algorithm: 'det' }
    calls.create.mockResolvedValue(running)
    const wrapper = mount(DeploymentsView, { global: { stubs } })
    await flushPromises()
    const selects = wrapper.findAll('.training-form select')
    expect((selects[0]!.element as HTMLSelectElement).value).toBe('det')
    await selects[1]!.setValue('dell')
    await wrapper.findAll('.training-form select')[2]!.setValue('0')
    await wrapper.findAll('button').find((button) => button.text() === '开始部署')!.trigger('click')
    await flushPromises()
    expect(calls.create).toHaveBeenCalledWith(expect.objectContaining({ algorithm_version_id: 'det', node_id: 'dell', gpu_device_ids: ['0'], runtime_profile: expect.objectContaining({ defaults: { confidence: 0.5 }, threads: 1, allowed_overrides: null }) }))
  })

  it('hides the GPU choice for CPU algorithms and blocks GPU algorithms on servers without one', async () => {
    const wrapper = mount(DeploymentsView, { global: { stubs } })
    await flushPromises()
    await wrapper.findAll('button').find((button) => button.text() === '新建部署')!.trigger('click')
    await flushPromises()
    await wrapper.findAll('.training-form select')[1]!.setValue('local')
    expect(wrapper.text()).toContain('所选服务器没有检测到 GPU')
    await wrapper.findAll('.training-form select')[0]!.setValue('ocr')
    await flushPromises()
    expect(wrapper.findAll('.deployment-form-grid select')).toHaveLength(2)
    expect(wrapper.text()).not.toContain('所选服务器没有检测到 GPU')
  })

  it('confirms before redeploying', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const wrapper = mount(DeploymentsView, { global: { stubs } })
    await flushPromises()
    await wrapper.findAll('.deployment-card')[0]!.findAll('button').find((button) => button.text() === '重新部署')!.trigger('click')
    expect(calls.redeploy).not.toHaveBeenCalled()
    confirm.mockReturnValue(true)
    await wrapper.findAll('.deployment-card')[0]!.findAll('button').find((button) => button.text() === '停止')!.trigger('click')
    await flushPromises()
    expect(calls.stop).toHaveBeenCalledWith('d1')
  })

  it('configures service route and supports rollback', async () => {
    calls.listRoutes.mockResolvedValue([
      {
        id: 'r1',
        key: 'helmet-prod',
        name: '安全帽线上服务',
        active_deployment_id: 'd1',
        active_algorithm_version_id: 'det',
        previous_deployment_id: 'd2',
        previous_algorithm_version_id: 'det',
        candidate_deployment_id: null,
        candidate_algorithm_version_id: null,
        rollout_status: 'active',
        drain_timeout_seconds: 60,
        last_error: null,
        updated_by: 'admin',
        created_at: '2026-10-23T00:00:00Z',
        updated_at: '2026-10-23T00:00:00Z',
      },
    ])
    calls.saveRoute.mockResolvedValue({ key: 'helmet-prod', rollout_status: 'switching' })
    calls.rollbackRoute.mockResolvedValue({ key: 'helmet-prod', rollout_status: 'active' })
    const wrapper = mount(DeploymentsView, { global: { stubs } })
    await flushPromises()
    expect(wrapper.text()).toContain('安全帽线上服务（helmet-prod）')
    await wrapper.findAll('button').find((button) => button.text() === '回滚上一版本')!.trigger('click')
    await flushPromises()
    expect(calls.rollbackRoute).toHaveBeenCalledWith('helmet-prod')
    await wrapper.findAll('button').find((button) => button.text() === '配置服务路由')!.trigger('click')
    await wrapper.find('input[placeholder*="helmet-prod"]').setValue('helmet-prod')
    await wrapper.find('input[placeholder*="安全帽线上服务"]').setValue('安全帽生产')
    await wrapper.findAll('button').find((button) => button.text() === '保存服务路由')!.trigger('click')
    await flushPromises()
    expect(calls.saveRoute).toHaveBeenCalledWith('helmet-prod', {
      name: '安全帽生产',
      target_deployment_id: 'd1',
      drain_timeout_seconds: 120,
    })
  })
})
