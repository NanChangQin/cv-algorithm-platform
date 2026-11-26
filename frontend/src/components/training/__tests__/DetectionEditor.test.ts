import { mount } from '@vue/test-utils'
import { defineComponent, ref } from 'vue'
import { afterEach, expect, it, vi } from 'vitest'
import DetectionEditor from '../DetectionEditor.vue'
import type { DetectionBox } from '@/api/evaluations'
vi.mock('@/api/assets', () => ({ assetsApi: { contentUrl: () => '/image.jpg' } }))
afterEach(() => vi.unstubAllGlobals())
it('draws a new box without changing the preceding annotation, then resizes and deletes it', async () => {
  vi.stubGlobal('DOMPoint', class {
    constructor(public x: number, public y: number) {}
    matrixTransform() { return this }
  })
  const original: DetectionBox = { label: 'person', bbox: [50, 50, 80, 80] }
  const boxes = ref<DetectionBox[]>([structuredClone(original)])
  const host = defineComponent({ components: { DetectionEditor }, setup: () => ({ boxes }), template: '<DetectionEditor v-model="boxes" asset-id="asset" :width="200" :height="200" :suggestions="[]" />' })
  const wrapper = mount(host)
  const svg = wrapper.find('svg')
  Object.assign(svg.element, { getScreenCTM: () => ({ inverse: () => ({}) }), setPointerCapture: () => {} })
  await wrapper.find('[aria-label="新增框类别"]').setValue('cat')
  await wrapper.findAll('button').find(b => b.text() === '绘制新框')!.trigger('click')
  await svg.trigger('pointerdown', { button: 0, clientX: 10, clientY: 20, pointerId: 1 })
  await svg.trigger('pointermove', { clientX: 100, clientY: 120, pointerId: 1 })
  await svg.trigger('pointerup', { pointerId: 1 })
  expect(boxes.value).toEqual([original, { label: 'cat', bbox: [10, 20, 100, 120] }])
  await wrapper.findAll('button').find(b => b.text() === '选择 / 移动框')!.trigger('click')
  const handle = wrapper.findAll('g').at(-1)!.findAll('rect').at(-1)!
  await handle.trigger('pointerdown', { button: 0, clientX: 100, clientY: 120, pointerId: 1 })
  await svg.trigger('pointermove', { clientX: 130, clientY: 150, pointerId: 1 })
  await svg.trigger('pointerup', { pointerId: 1 })
  expect(boxes.value[1]!.bbox).toEqual([10, 20, 130, 150])
  await wrapper.findAll('button').find(b => b.text() === '删除选中框')!.trigger('click')
  expect(boxes.value).toEqual([original])
})
