import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/api/assets', () => ({ assetsApi: { thumbnailUrl: () => '' } }))
import ImageFilmstrip from '@/components/workbench/ImageFilmstrip.vue'

const items = ['a', 'b', 'c'].map((id) => ({ id, original_name: `${id}.png`, width: 10, height: 10 }))
function render(selected: string[], focused: string | null) {
  return mount(ImageFilmstrip, { props: { items, total: 3, query: '', selected, focused } })
}

describe('image filmstrip selection', () => {
  it('selects only the clicked image when it was not selected', async () => {
    const wrapper = render(['a'], 'a')
    await wrapper.findAll('.filmstrip-thumb')[1]!.trigger('click')
    expect(wrapper.emitted('update:selected')).toEqual([[['b']]])
    expect(wrapper.emitted('update:focused')).toEqual([['b']])
  })

  it('only moves the preview when clicking an image that is already part of a multi-selection', async () => {
    const wrapper = render(['a', 'b'], 'a')
    await wrapper.findAll('.filmstrip-thumb')[1]!.trigger('click')
    expect(wrapper.emitted('update:selected')).toBeUndefined()
    expect(wrapper.emitted('update:focused')).toEqual([['b']])
  })

  it('adds images with Ctrl-click or the corner button and never drops the last one', async () => {
    const wrapper = render(['a'], 'a')
    await wrapper.findAll('.filmstrip-thumb')[2]!.trigger('click', { ctrlKey: true })
    expect(wrapper.emitted('update:selected')).toEqual([[['a', 'c']]])
    await wrapper.findAll('.filmstrip-check')[0]!.trigger('click')
    expect(wrapper.emitted('update:selected')).toHaveLength(1)
  })
})
