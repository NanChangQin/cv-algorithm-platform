import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ upload: vi.fn() }))
vi.mock('@/api/assets', () => ({ assetsApi: api }))

import { ApiError } from '@/api/http'
import { uploadFailureMessage, uploadInBatches } from '../useAssets'

function asset(name: string) { return { id: `id-${name}`, original_name: name } }
function file(name: string) { return new File(['x'], name, { type: 'image/png' }) }

describe('chunked image upload', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('isolates the invalid file inside a rejected batch and keeps the others', async () => {
    api.upload.mockImplementation(async (files: File[]) => {
      if (files.some((item) => item.name === 'bad.gif')) throw new ApiError('仅支持 JPG、PNG、BMP 和 WebP 图片', 400, 'INPUT_INVALID')
      return { assets: files.map((item) => asset(item.name)) }
    })
    const uploaded: string[] = []

    const outcome = await uploadInBatches([file('a.png'), file('bad.gif'), file('c.png')], {
      onUploaded: (assets) => uploaded.push(...assets.map((item) => item.original_name)),
    })

    expect(outcome.uploaded.map((item) => item.original_name)).toEqual(['a.png', 'c.png'])
    expect(uploaded).toEqual(['a.png', 'c.png'])
    expect(outcome.failed).toEqual([{ name: 'bad.gif', message: '仅支持 JPG、PNG、BMP 和 WebP 图片' }])
    expect(uploadFailureMessage(outcome.failed)).toBe('bad.gif 上传失败：仅支持 JPG、PNG、BMP 和 WebP 图片')
  })

  it('sends large selections in batches of ten', async () => {
    api.upload.mockImplementation(async (files: File[]) => ({ assets: files.map((item) => asset(item.name)) }))
    await uploadInBatches(Array.from({ length: 23 }, (_, index) => file(`${index}.png`)))
    expect(api.upload.mock.calls.map(([files]) => files.length)).toEqual([10, 10, 3])
  })

  it('does not retry one by one when the network fails', async () => {
    api.upload.mockRejectedValue(new ApiError('网络连接中断，上传失败', 0, 'NETWORK_ERROR'))
    const outcome = await uploadInBatches([file('a.png'), file('b.png')])
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
