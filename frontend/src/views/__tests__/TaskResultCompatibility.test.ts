import { flushPromises, shallowMount } from '@vue/test-utils'
import { ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { id: 'legacy-task' } }),
  useRouter: () => ({ push: vi.fn() }),
}))
vi.mock('@/api/tasks', () => ({ tasksApi: {} }))
vi.mock('@/api/system', () => ({ systemApi: { executionResources: vi.fn().mockResolvedValue({ nodes: [] }) } }))
vi.mock('@/api/assets', () => ({ assetsApi: { contentUrl: (id: string) => `/assets/${id}`, get: vi.fn() } }))
vi.mock('@/composables/useAlgorithms', () => ({ useAlgorithms: () => ({ algorithms: ref([]), availableAlgorithms: ref([]) }) }))
vi.mock('@/composables/useTaskWatch', () => ({ useTaskWatch: () => ({
  task: ref({
    id: 'legacy-task', status: 'completed', asset_id: null, parameters: {},
    node_id: null, execution_node_id: null, gpu_device_ids: [], execution_gpu_device_ids: [],
  }),
  result: ref({ type: 'classification', data: { predictions: [{ label: 'cat', score: 0.9 }] } }),
  error: ref(''), terminal: ref(true), reload: vi.fn(), replace: vi.fn(),
}) }))

import ResultViewer from '@/components/results/ResultViewer.vue'
import TaskDrawer from '@/components/tasks/TaskDrawer.vue'
import TaskDetailView from '../TaskDetailView.vue'

describe('historical task results', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders a completed result on the detail page even without a linked image asset', async () => {
    const wrapper = shallowMount(TaskDetailView, { global: { stubs: { RouterLink: true } } })
    await flushPromises()
    expect(wrapper.findComponent(ResultViewer).exists()).toBe(true)
    expect(wrapper.findComponent(ResultViewer).props('imageUrl')).toBe('')
    expect(wrapper.text()).not.toContain('正在读取推理结果')
    wrapper.unmount()
  })

  it('renders a completed historical result in the drawer too', async () => {
    const wrapper = shallowMount(TaskDrawer, {
      props: { taskId: 'legacy-task', algorithms: [] },
      global: { stubs: { RouterLink: true } },
    })
    await flushPromises()
    expect(wrapper.findComponent(ResultViewer).exists()).toBe(true)
    expect(wrapper.findComponent(ResultViewer).props('imageUrl')).toBe('')
    expect(wrapper.text()).not.toContain('正在读取推理结果')
    wrapper.unmount()
  })
})
