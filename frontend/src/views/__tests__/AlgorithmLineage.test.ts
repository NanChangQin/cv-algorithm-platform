import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

vi.mock('vue-router', () => ({ useRoute: () => ({ query: { selected: 'v2' } }) }))
vi.mock('@/api/auth', () => ({ sessionRole: { get: () => 'admin' } }))
vi.mock('@/api/algorithms', () => ({ algorithmsApi: {} }))
const lineage = (map50_95: number, checkpoint: string, dataset: string) => ({
  training_run_id: `run-${checkpoint}`, training_name: '安全帽 · yolo26n', dataset_id: 'ds', dataset_name: dataset,
  class_names: ['helmet', 'head'], base_model: 'yolo26n.pt', checkpoint, epoch: 40, epochs_trained: 50,
  dataset: { id: 'ds', version: 2, sha256: 'dataset-normalized-hash', source_sha256: 'source-hash', split_config: { seed: 42 } },
  parameters: { epochs: 50, batch: 16, imgsz: 640, lr0: 0.01 },
  metrics: { precision: 0.8, recall: 0.7, map50: 0.75, map50_95 },
})
vi.mock('@/composables/useAlgorithms', () => ({ useAlgorithms: () => {
  const base = { key: 'helmet', name: '安全帽检测', description: '', task_type: 'object_detection', device: 'gpu', framework: 'pytorch', image: 'cv-platform/yolo:dev', parameters: {}, package_sha256: null, image_digest: null, created_by: 'admin', traffic_weight: 100, container_status: 'stopped', last_called_at: null, project_id: 'p' }
  const algorithms = ref([
    { ...base, id: 'v1', version: '1.0.0', status: 'disabled', created_at: '2026-10-20T00:00:00Z', lineage: lineage(0.52, 'best', '安全帽 v1') },
    { ...base, id: 'v2', version: '1.0.1', status: 'available', created_at: '2026-10-22T00:00:00Z', lineage: lineage(0.47, 'last', '安全帽 v2') },
  ])
  return { algorithms, availableAlgorithms: algorithms, loading: ref(false), error: ref(''), refresh: vi.fn() }
} }))

import AlgorithmsView from '../AlgorithmsView.vue'

describe('trained model versions', () => {
  it('opens the linked version with its lineage and compares the family', async () => {
    const wrapper = mount(AlgorithmsView, { global: { stubs: { RouterLink: { props: ['to'], template: '<a :data-to="to"><slot /></a>' }, AlgorithmCard: true, UploadDropzone: true } } })
    await flushPromises()
    const detail = wrapper.find('.algorithm-detail')
    expect(detail.find('h2').text()).toBe('安全帽检测')
    const lineageText = detail.find('.lineage-list').text()
    expect(lineageText).toContain('安全帽 v2 · v2 · 2 类')
    expect(lineageText).toContain('dataset-normalized-hash')
    expect(lineageText).toContain('yolo26n 微调 · last.pt（第 40/50 轮）')
    expect(detail.find('.lineage-list a').attributes('data-to')).toBe('/training/run-last')
    const rows = detail.findAll('.version-compare tbody tr')
    expect(rows.map((row) => row.find('td').text())).toEqual(['v1.0.1', 'v1.0.0'])
    expect(rows[0]!.classes()).toContain('current')
    // 指标最好的版本高亮，方便决定是否回滚
    expect(rows[1]!.find('td.best').text()).toBe('52.0')
    await rows[1]!.trigger('click')
    expect(wrapper.find('.algorithm-detail .count-badge').text()).toBe('v1.0.0')
    expect(wrapper.findAll('button').some((button) => button.text() === '回滚到此版本')).toBe(true)
  })
})
