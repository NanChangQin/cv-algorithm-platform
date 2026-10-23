import { flushPromises, shallowMount } from '@vue/test-utils'
import { ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import TaskDrawer from '@/components/tasks/TaskDrawer.vue'
import TaskTable from '@/components/tasks/TaskTable.vue'

const calls = vi.hoisted(() => ({ archive: vi.fn(), resources: vi.fn(), page: vi.fn(), summary: vi.fn(), byIds: vi.fn(), replace: vi.fn(), datasets: vi.fn(), datasetFeedback: vi.fn() }))
vi.mock('vue-router', () => ({ useRoute: () => ({ query: {} }), useRouter: () => ({ replace: calls.replace }) }))
vi.mock('@/api/tasks', () => ({ tasksApi: { archive: calls.archive, page: calls.page, summary: calls.summary, byIds: calls.byIds } }))
vi.mock('@/api/training', () => ({ trainingApi: { datasets: calls.datasets, datasetFeedback: calls.datasetFeedback } }))
vi.mock('@/api/assets', () => ({ assetsApi: { byIds: vi.fn().mockResolvedValue([{ id: 'asset-1', original_name: 'bear.jpg' }]), thumbnailUrl: () => '' } }))
vi.mock('@/api/system', () => ({ systemApi: { executionResources: calls.resources } }))
vi.mock('@/composables/useAlgorithms', () => ({ useAlgorithms: () => ({ algorithms: ref([{ id: 'algorithm-1', name: 'Detector', version: '1' }]) }) }))
import TasksView from '../TasksView.vue'

const tasks = [
  { id: 'task-1', algorithm_version_id: 'algorithm-1', asset_id: 'asset-1', status: 'completed', execution_node_id: 'dell', gpu_device_ids: [], execution_gpu_device_ids: ['0'] },
  { id: 'task-2', algorithm_version_id: 'algorithm-1', asset_id: null, status: 'running', node_id: 'local', gpu_device_ids: ['0'], execution_gpu_device_ids: [] },
]

describe('task center', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    calls.resources.mockResolvedValue({ nodes: [{ id: 'dell', name: 'Dell GPU' }, { id: 'local', name: 'Local' }] })
    calls.page.mockResolvedValue({ items: tasks, total: 2 })
    calls.summary.mockResolvedValue({ total: 7, by_status: { queued: 1, preparing: 0, starting: 0, running: 1, completed: 4, failed: 1, cancelled: 0 } })
    calls.byIds.mockResolvedValue([])
  })
  afterEach(() => { vi.useRealTimers() })

  it('lets the server search by filename and filter by server', async () => {
    vi.useFakeTimers()
    const wrapper = shallowMount(TasksView, { global: { stubs: { RouterLink: true } } })
    await flushPromises()
    expect(wrapper.findComponent(TaskTable).props('tasks')).toHaveLength(2)
    await wrapper.find('input[type="search"]').setValue('bear.jpg')
    await vi.advanceTimersByTimeAsync(300)
    expect(calls.page).toHaveBeenLastCalledWith(expect.objectContaining({ q: 'bear.jpg', offset: 0, limit: 20 }))
    await wrapper.find('[aria-label="按服务器筛选"]').setValue('local')
    await flushPromises()
    expect(calls.page).toHaveBeenLastCalledWith(expect.objectContaining({ q: 'bear.jpg', node_id: 'local' }))
    wrapper.unmount()
  })

  it('shows how many tasks each status has and filters by tab', async () => {
    const wrapper = shallowMount(TasksView, { global: { stubs: { RouterLink: true } } })
    await flushPromises()
    const tabs = wrapper.findAll('.status-tabs button')
    expect(tabs.map((tab) => tab.text())).toEqual(['全部7', '进行中2', '已完成4', '失败1', '已取消0'])
    await tabs[1]!.trigger('click')
    await flushPromises()
    expect(calls.page).toHaveBeenLastCalledWith(expect.objectContaining({ status: ['queued', 'preparing', 'starting', 'running'] }))
    wrapper.unmount()
  })

  it('opens a task in the side drawer instead of leaving the list', async () => {
    const wrapper = shallowMount(TasksView, { global: { stubs: { RouterLink: true } } })
    await flushPromises()
    expect(wrapper.findComponent(TaskDrawer).exists()).toBe(false)
    wrapper.findComponent(TaskTable).vm.$emit('open', 'task-1')
    await flushPromises()
    expect(wrapper.findComponent(TaskDrawer).props('taskId')).toBe('task-1')
    expect(wrapper.findComponent(TaskDrawer).props('asset')).toEqual({ id: 'asset-1', original_name: 'bear.jpg' })
    expect(calls.replace).toHaveBeenLastCalledWith({ query: { task: 'task-1' } })
    wrapper.findComponent(TaskDrawer).vm.$emit('close')
    await flushPromises()
    expect(wrapper.findComponent(TaskDrawer).exists()).toBe(false)
    wrapper.unmount()
  })

  it('refreshes only the running tasks on the page', async () => {
    vi.useFakeTimers()
    calls.byIds.mockResolvedValue([{ ...tasks[1], status: 'completed' }])
    const wrapper = shallowMount(TasksView, { global: { stubs: { RouterLink: true } } })
    await flushPromises()
    await vi.advanceTimersByTimeAsync(2500)
    expect(calls.byIds).toHaveBeenCalledWith(['task-2'])
    expect(wrapper.findComponent(TaskTable).props('tasks')[1].status).toBe('completed')
    expect(calls.summary).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })

  it('removes finished tasks from the active tab and reloads the filtered total', async () => {
    vi.useFakeTimers()
    calls.page.mockResolvedValue({ items: [tasks[1]], total: 1 })
    calls.byIds.mockResolvedValue([{ ...tasks[1], status: 'completed' }])
    const wrapper = shallowMount(TasksView, { global: { stubs: { RouterLink: true } } })
    await flushPromises()
    await wrapper.findAll('.status-tabs button')[1]!.trigger('click')
    await flushPromises()
    calls.page.mockResolvedValue({ items: [], total: 0 })
    await vi.advanceTimersByTimeAsync(2500)
    await flushPromises()
    expect(calls.page).toHaveBeenLastCalledWith(expect.objectContaining({
      status: ['queued', 'preparing', 'starting', 'running'],
    }))
    expect(wrapper.findComponent(TaskTable).exists()).toBe(false)
    expect(wrapper.find('.toolbar').text()).toContain('0 个任务')
    wrapper.unmount()
  })

  it('returns to the preceding page when cancelling the last matching task', async () => {
    calls.page.mockResolvedValue({ items: [tasks[1]], total: 21 })
    const wrapper = shallowMount(TasksView, { global: { stubs: { RouterLink: true } } })
    await flushPromises()
    await wrapper.findAll('.status-tabs button')[1]!.trigger('click')
    await flushPromises()
    await wrapper.findAll('.pagination button')[1]!.trigger('click')
    await flushPromises()
    expect(calls.page).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 20 }))
    wrapper.findComponent(TaskTable).vm.$emit('open', 'task-2')
    await flushPromises()
    calls.page.mockResolvedValue({ items: [tasks[1]], total: 20 })
    wrapper.findComponent(TaskDrawer).vm.$emit('updated', { ...tasks[1], status: 'cancelled' })
    await flushPromises()
    expect(calls.page).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 0 }))
    expect(wrapper.find('.toolbar').text()).toContain('20 个任务')
    wrapper.unmount()
  })

  it('does not overwrite a refreshed page with an older polling response', async () => {
    vi.useFakeTimers()
    let finishPoll!: (value: unknown[]) => void
    calls.byIds.mockReturnValue(new Promise((resolve) => { finishPoll = resolve }))
    const wrapper = shallowMount(TasksView, { global: { stubs: { RouterLink: true } } })
    await flushPromises()
    await vi.advanceTimersByTimeAsync(2500)
    calls.page.mockResolvedValue({ items: [{ ...tasks[1], status: 'completed' }], total: 1 })
    await wrapper.findAll('button').find((button) => button.text() === '刷新')!.trigger('click')
    await flushPromises()
    finishPoll([{ ...tasks[1], status: 'running' }])
    await flushPromises()
    expect(wrapper.findComponent(TaskTable).props('tasks')[0].status).toBe('completed')
    wrapper.unmount()
  })

  it('reports archive failures and preserves the selected results', async () => {
    calls.archive.mockRejectedValue(new Error('下载服务暂不可用'))
    const wrapper = shallowMount(TasksView, { global: { stubs: { RouterLink: true } } })
    await flushPromises()
    const selectPage = wrapper.findAll('button').find((button) => button.text() === '选择本页已完成结果')!
    await selectPage.trigger('click')
    const download = wrapper.findAll('button').find((button) => button.text() === '下载结果 ZIP')!
    await download.trigger('click'); await flushPromises()
    expect(calls.archive).toHaveBeenCalledWith(['task-1'])
    expect(wrapper.text()).toContain('下载服务暂不可用')
    expect(wrapper.text()).toContain('已选 1 项')
    wrapper.unmount()
  })

  it('feeds selected completed tasks back into a training dataset to create a new version', async () => {
    calls.datasets.mockResolvedValue([
      { id: 'ds-1', name: '安全帽数据集', version: 1, class_names: ['helmet', 'person'] },
    ])
    calls.datasetFeedback.mockResolvedValue({
      id: 'ds-2',
      name: '安全帽数据集',
      version: 2,
      class_names: ['helmet', 'person'],
    })
    const wrapper = shallowMount(TasksView, { global: { stubs: { RouterLink: true } } })
    await flushPromises()
    await wrapper.findAll('button').find((button) => button.text() === '选择本页已完成结果')!.trigger('click')
    await wrapper.findAll('button').find((button) => button.text() === '回流至数据集')!.trigger('click')
    await flushPromises()
    expect(calls.datasets).toHaveBeenCalled()
    await wrapper.find('[aria-label="回流目标划分"]').setValue('val')
    await wrapper.findAll('button').find((button) => button.text().includes('确认回流'))!.trigger('click')
    await flushPromises()
    expect(calls.datasetFeedback).toHaveBeenCalledWith('ds-1', {
      samples: [{ task_id: 'task-1' }],
      target_split: 'val',
      min_confidence: 0.25,
    })
    wrapper.unmount()
  })
})
