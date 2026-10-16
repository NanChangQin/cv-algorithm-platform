import { shallowMount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import TaskTable from '../TaskTable.vue'
import type { InferenceTask } from '@/types/task'

describe('historical task placement', () => {
  it('does not present an old completed task as waiting for execution', () => {
    const task = { id: 'old-task', algorithm_version_id: 'algorithm', created_at: '2026-08-01T00:00:00Z', status: 'completed', node_id: null, execution_node_id: null } as InferenceTask
    const wrapper = shallowMount(TaskTable, { props: { tasks: [task], algorithms: [] }, global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } } })
    expect(wrapper.text()).toContain('未记录位置')
    expect(wrapper.text()).not.toContain('等待执行')
    wrapper.unmount()
  })
})
