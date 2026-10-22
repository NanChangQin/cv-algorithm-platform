import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ list: vi.fn(), upload: vi.fn() }))
vi.mock('@/api/assets', () => ({ assetsApi: api }))

import { ApiError } from '@/api/http'
import { uploadInBatches, useAssets } from '../useAssets'

function asset(name: string) {
  return { id: `id-${name}`, original_name: name, created_at: `2026-10-21T00:00:0${name.length % 10}Z` }
}
function file(name: string) { return new File(['x'], name, { type: 'image/png' }) }
function setup() {
  let state!: ReturnType<typeof useAssets>
  mount(defineComponent({ setup() { state = useAssets(); return () => null } }))
  return state
}

describe('chunked image upload', () => {
  beforeEach(() => { vi.clearAllMocks(); api.list.mockResolvedValue([]) })

  it('isolates the invalid file inside a rejected batch and keeps the others', async () => {
    api.upload.mockImplementation(async (files: File[]) => {
      if (files.some((item) => item.name === 'bad.gif')) throw new ApiError('仅支持 JPG、PNG、BMP 和 WebP 图片', 400, 'INPUT_INVALID')
      return { assets: files.map((item) => asset(item.name)) }
    })
    const state = setup()
    await flushPromises()

    const outcome = await state.upload([file('a.png'), file('bad.gif'), file('c.png')])

    expect(outcome.uploaded.map((item) => item.original_name)).toEqual(['a.png', 'c.png'])
    expect(outcome.failed).toEqual([{ name: 'bad.gif', message: '仅支持 JPG、PNG、BMP 和 WebP 图片' }])
    expect(state.assets.value).toHaveLength(2)
    expect(state.error.value).toContain('bad.gif')
    expect(state.uploading.value).toBe(false)
    expect(state.uploadProgress.value).toBeNull()
  })

  it('sends large selections in batches of ten', async () => {
    api.upload.mockImplementation(async (files: File[]) => ({ assets: files.map((item) => asset(item.name)) }))
    const state = setup()
    await flushPromises()
    await state.upload(Array.from({ length: 23 }, (_, index) => file(`${index}.png`)))
    expect(api.upload.mock.calls.map(([files]) => files.length)).toEqual([10, 10, 3])
  })

  it('does not retry one by one when the network fails', async () => {
    api.upload.mockRejectedValue(new ApiError('网络连接中断，上传失败', 0, 'NETWORK_ERROR'))
    const state = setup()
    await flushPromises()
    const outcome = await state.upload([file('a.png'), file('b.png')])
    expect(api.upload).toHaveBeenCalledTimes(1)
    expect(outcome.failed).toHaveLength(2)
  })

  it('associates successful assets with source filenames after single-file retries', async () => {
    api.upload.mockImplementation(async (files: File[]) => {
      if (files.some((item) => item.name === 'bad.gif')) throw new ApiError('invalid', 400, 'INPUT_INVALID')
      return { assets: files.map(() => asset('original.png')) }
    })
    const onUploaded = vi.fn()
    const files = [file('renamed.png'), file('bad.gif'), file('another.png')]
    await uploadInBatches(files, { onUploaded })
    expect(onUploaded).toHaveBeenCalledTimes(2)
    expect(onUploaded.mock.calls[0]![1]).toEqual([files[0]])
    expect(onUploaded.mock.calls[1]![1]).toEqual([files[2]])
  })
})
