import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/api/assets', () => ({ assetsApi: { thumbnailUrl: (id: string) => `/thumb/${id}` } }))
import AssetPicker from '../AssetPicker.vue'
import UploadDropzone from '../UploadDropzone.vue'

const assets = ['bear.jpg', 'cat.png', 'dog.jpg', 'bird.jpg', 'fish.jpg', 'frog.jpg', 'owl.jpg'].map((name, index) => ({
  id: `asset-${index}`, original_name: name, width: 10, height: 10,
}))

describe('AssetPicker', () => {
  it('selects a single image on click and builds a batch with the corner toggles', async () => {
    const wrapper = mount(AssetPicker, { props: { assets, modelValue: ['asset-0'], multiple: true, max: 2 } })
    await wrapper.findAll('.asset-option-main')[1]!.trigger('click')
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual([['asset-1']])
    await wrapper.findAll('.asset-option-main')[2]!.trigger('click', { ctrlKey: true })
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual([['asset-0', 'asset-2']])

    await wrapper.setProps({ modelValue: ['asset-1', 'asset-3'] })
    expect(wrapper.findAll('.asset-option-check')[3]!.text()).toBe('2')
    const before = wrapper.emitted('update:modelValue')!.length
    await wrapper.findAll('.asset-option-check')[4]!.trigger('click')
    expect(wrapper.emitted('update:modelValue')).toHaveLength(before)
    await wrapper.findAll('.asset-option-check')[1]!.trigger('click')
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual([['asset-3']])
  })

  it('filters by file name and selects the matches', async () => {
    const wrapper = mount(AssetPicker, { props: { assets, modelValue: [], multiple: true } })
    await wrapper.find('input[type="search"]').setValue('.png')
    expect(wrapper.findAll('.asset-option')).toHaveLength(1)
    await wrapper.findAll('button').find((button) => button.text() === '选择搜索结果')!.trigger('click')
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual([['asset-1']])
  })
})

describe('UploadDropzone', () => {
  it('delivers dropped files that match the accepted types', async () => {
    const wrapper = mount(UploadDropzone)
    const image = new File(['x'], 'photo.png', { type: 'image/png' })
    const text = new File(['x'], 'notes.txt', { type: 'text/plain' })
    await wrapper.trigger('drop', { dataTransfer: { files: [image, text] } })
    expect(wrapper.emitted('files')).toEqual([[[image]]])
  })

  it('ignores paste events aimed at text inputs', async () => {
    const wrapper = mount(UploadDropzone, { props: { acceptPaste: true }, attachTo: document.body })
    const input = document.createElement('input')
    document.body.appendChild(input)
    const image = new File(['x'], 'image.png', { type: 'image/png' })
    const paste = (target: EventTarget) => {
      const event = new Event('paste', { bubbles: true }) as ClipboardEvent
      Object.defineProperty(event, 'clipboardData', { value: { files: [image] } })
      target.dispatchEvent(event)
    }
    paste(input)
    expect(wrapper.emitted('files')).toBeUndefined()
    paste(document.body)
    const pasted = wrapper.emitted('files')![0]![0] as File[]
    expect(pasted[0]!.name).toMatch(/^粘贴-\d{14}\.png$/)
    input.remove()
    wrapper.unmount()
  })
})
