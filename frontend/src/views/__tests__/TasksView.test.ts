import { flushPromises, shallowMount } from '@vue/test-utils'
import { ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import TaskTable from '@/components/tasks/TaskTable.vue'

const calls = vi.hoisted(() => ({ archive: vi.fn(), resources: vi.fn() }))
vi.mock('@/api/tasks', () => ({ tasksApi: { archive: calls.archive } }))
vi.mock('@/api/system', () => ({ systemApi: { executionResources: calls.resources } }))
vi.mock('@/composables/useAlgorithms', () => ({ useAlgorithms: () => ({ algorithms: ref([{ id: 'algorithm-1', name: 'Detector' }]) }) }))
vi.mock('@/composables/useAssets', () => ({ useAssets: () => ({ assets: ref([{ id: 'asset-1', original_name: 'bear.jpg' }]) }) }))
vi.mock('@/composables/useTasks', () => ({ useTasks: () => ({
  tasks: ref([
    { id: 'task-1', algorithm_version_id: 'algorithm-1', asset_id: 'asset-1', status: 'completed', execution_node_id: 'dell', gpu_device_ids: [], execution_gpu_device_ids: ['0'] },
    { id: 'task-2', algorithm_version_id: 'algorithm-1', asset_id: null, status: 'running', node_id: 'local', gpu_device_ids: ['0'], execution_gpu_device_ids: [] },
  ]), loading: ref(false), pendingId: ref(''), error: ref(''), refresh: vi.fn(), cancel: vi.fn(), retry: vi.fn(),
}) }))
import TasksView from '../TasksView.vue'

describe('task discovery and exports', () => {
  beforeEach(() => { vi.clearAllMocks(); calls.resources.mockResolvedValue({ nodes: [{ id: 'dell', name: 'Dell GPU' }, { id: 'local', name: 'Local' }] }) })

  it('finds tasks by input filename and server without requiring ids', async () => {
    const wrapper = shallowMount(TasksView, { global: { stubs: { RouterLink: true } } })
    await flushPromises()
    await wrapper.find('input[type="search"]').setValue('bear.jpg')
    expect(wrapper.findComponent(TaskTable).props('tasks')).toHaveLength(1)
    await wrapper.find('input[type="search"]').setValue('')
    await wrapper.find('[aria-label="按服务器筛选"]').setValue('local')
    expect(wrapper.findComponent(TaskTable).props('tasks')[0].id).toBe('task-2')
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
})
