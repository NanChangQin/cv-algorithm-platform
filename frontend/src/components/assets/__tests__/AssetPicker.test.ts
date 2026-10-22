import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ page: vi.fn() }))
vi.mock('@/api/assets', () => ({ assetsApi: { page: api.page, thumbnailUrl: (id: string) => `/thumb/${id}` } }))
import AssetPicker from '../AssetPicker.vue'
import UploadDropzone from '../UploadDropzone.vue'

function asset(index: number, name = `frame_${String(index).padStart(3, '0')}.jpg`) {
  return { id: `asset-${index}`, original_name: name, width: 10, height: 10 }
}
const library = Array.from({ length: 30 }, (_, index) => asset(index))
/** 和服务端一致：文件名包含搜索词（不区分大小写），再按 offset/limit 分页 */
async function serve({ q, limit, offset = 0 }: { q?: string; limit: number; offset?: number }) {
  const matches = library.filter((item) => !q || item.original_name.toLowerCase().includes(q.toLowerCase()))
  return { items: matches.slice(offset, offset + limit), total: matches.length }
}
const names = (wrapper: VueWrapper) => wrapper.findAll('.asset-option small').map((node) => node.text())

describe('AssetPicker', () => {
  beforeEach(() => { api.page.mockReset().mockImplementation(serve) })
  afterEach(() => { vi.useRealTimers() })

  it('reads one page at a time from the server', async () => {
    const wrapper = mount(AssetPicker, { props: { modelValue: null } })
    await flushPromises()
    expect(api.page).toHaveBeenCalledTimes(1)
    expect(api.page).toHaveBeenCalledWith(expect.objectContaining({ limit: 24, offset: 0 }))
    expect(names(wrapper)).toHaveLength(24)
    expect(wrapper.find('.asset-picker-more').text()).toBe('显示更多（还有 6 张）')

    await wrapper.find('.asset-picker-more').trigger('click')
    await flushPromises()
    expect(api.page).toHaveBeenLastCalledWith(expect.objectContaining({ limit: 24, offset: 24 }))
    expect(names(wrapper)).toHaveLength(30)
    expect(wrapper.find('.asset-picker-more').exists()).toBe(false)
  })

  it('searches file names on the server and keeps the search box while editing', async () => {
    vi.useFakeTimers()
    const wrapper = mount(AssetPicker, { props: { modelValue: null } })
    await flushPromises()
    await wrapper.find('input[type="search"]').setValue('FRAME_02')
    await vi.advanceTimersByTimeAsync(250)
    expect(api.page).toHaveBeenLastCalledWith(expect.objectContaining({ q: 'FRAME_02', offset: 0 }))
    expect(names(wrapper)).toEqual(library.slice(20).map((item) => item.original_name))

    await wrapper.find('input[type="search"]').setValue('cat')
    await vi.advanceTimersByTimeAsync(250)
    expect(wrapper.text()).toContain('没有文件名包含“cat”的图片。')
    // 清空搜索词时，新结果回来之前搜索框也不能消失
    await wrapper.find('input[type="search"]').setValue('')
    expect(wrapper.find('input[type="search"]').exists()).toBe(true)
    await vi.advanceTimersByTimeAsync(250)
    expect(names(wrapper)).toHaveLength(24)

    // 放在表单里时，回车不能提交表单
    const enter = new KeyboardEvent('keydown', { key: 'Enter', cancelable: true })
    wrapper.find('input[type="search"]').element.dispatchEvent(enter)
    expect(enter.defaultPrevented).toBe(true)
  })

  it('keeps a linked image from a later page first without moving or repeating it', async () => {
    const linked = library[27]!
    const wrapper = mount(AssetPicker, { props: { modelValue: linked } })
    await flushPromises()
    expect(names(wrapper).slice(0, 2)).toEqual(['frame_027.jpg', 'frame_000.jpg'])
    expect(wrapper.find('.asset-option.selected').text()).toContain('frame_027.jpg')

    await wrapper.findAll('.asset-option-main')[2]!.trigger('click')
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual([library[1]])
    await wrapper.setProps({ modelValue: library[1]! })
    expect(names(wrapper)[0]).toBe('frame_027.jpg')
    expect(wrapper.find('.asset-picker-more').text()).toBe('显示更多（还有 5 张）')

    await wrapper.find('.asset-picker-more').trigger('click')
    await flushPromises()
    expect(names(wrapper)).toHaveLength(30)
    expect(names(wrapper).filter((name) => name === 'frame_027.jpg')).toHaveLength(1)
    expect(names(wrapper)[0]).toBe('frame_027.jpg')
    expect(wrapper.find('.asset-picker-more').exists()).toBe(false)
  })

  it('still shows an image picked from search results after the search is cleared', async () => {
    vi.useFakeTimers()
    const wrapper = mount(AssetPicker, { props: { modelValue: null } })
    await flushPromises()
    await wrapper.find('input[type="search"]').setValue('029')
    await vi.advanceTimersByTimeAsync(250)
    await wrapper.find('.asset-option-main').trigger('click')
    await wrapper.setProps({ modelValue: library[29]! })
    await wrapper.find('input[type="search"]').setValue('')
    await vi.advanceTimersByTimeAsync(250)
    expect(names(wrapper)[0]).toBe('frame_029.jpg')
    expect(wrapper.find('.asset-option.selected').text()).toContain('frame_029.jpg')
  })

  it('selects the newest image only when nothing is selected yet', async () => {
    const fresh = mount(AssetPicker, { props: { modelValue: null, selectFirst: true } })
    await flushPromises()
    expect(fresh.emitted('update:modelValue')).toEqual([[library[0]]])
    const linked = mount(AssetPicker, { props: { modelValue: library[27]!, selectFirst: true } })
    await flushPromises()
    expect(linked.emitted('update:modelValue')).toBeUndefined()
  })

  it('puts uploads in front of everything and clears the search so they show up', async () => {
    vi.useFakeTimers()
    const wrapper = mount(AssetPicker, { props: { modelValue: library[27]! } })
    await flushPromises()
    await wrapper.find('input[type="search"]').setValue('frame_01')
    await vi.advanceTimersByTimeAsync(250)
    const uploaded = asset(100, 'upload.png')
    wrapper.vm.add([uploaded])
    await wrapper.setProps({ modelValue: uploaded })
    await vi.advanceTimersByTimeAsync(250)
    expect((wrapper.find('input[type="search"]').element as HTMLInputElement).value).toBe('')
    expect(names(wrapper).slice(0, 3)).toEqual(['upload.png', 'frame_027.jpg', 'frame_000.jpg'])
    expect(wrapper.find('.asset-option.selected').text()).toContain('upload.png')
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
