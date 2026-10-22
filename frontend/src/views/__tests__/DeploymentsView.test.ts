import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const calls = vi.hoisted(() => ({
  list: vi.fn(), create: vi.fn(), stop: vi.fn(), start: vi.fn(), redeploy: vi.fn(), remove: vi.fn(), resources: vi.fn(),
  query: {} as Record<string, string>,
}))
vi.mock('vue-router', () => ({ useRoute: () => ({ query: calls.query }) }))
vi.mock('@/api/system', () => ({ systemApi: { executionResources: calls.resources } }))
vi.mock('@/api/deployments', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/deployments')>()),
  deploymentsApi: { list: calls.list, create: calls.create, stop: calls.stop, start: calls.start, redeploy: calls.redeploy, remove: calls.remove },
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
  beforeEach(() => {
    vi.clearAllMocks()
    calls.query = {}
    calls.list.mockResolvedValue([running, failed])
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
    expect(calls.create).toHaveBeenCalledWith({ algorithm_version_id: 'det', node_id: 'dell', gpu_device_ids: ['0'] })
  })

  it('hides the GPU choice for CPU algorithms and blocks GPU algorithms on servers without one', async () => {
    const wrapper = mount(DeploymentsView, { global: { stubs } })
    await flushPromises()
    await wrapper.findAll('button').find((button) => button.text() === '新建部署')!.trigger('click')
    await flushPromises()
    await wrapper.findAll('.training-form select')[1]!.setValue('local')
    expect(wrapper.text()).toContain('所选服务器没有检测到 GPU')
    await wrapper.findAll('.training-form select')[0]!.setValue('ocr')
    expect(wrapper.findAll('.training-form select')).toHaveLength(2)
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
})
