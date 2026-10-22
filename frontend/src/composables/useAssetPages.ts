import { computed, onMounted, onUnmounted, ref, watch } from 'vue'

import { assetsApi } from '@/api/assets'
import type { ImageAsset } from '@/types/asset'
import { errorMessage } from '@/utils/errors'

/**
 * 图片按页从服务端读取（视频抽帧后一个项目可能有上万张图）。
 * 搜索词变化后从第一页重新读取，“加载更多”追加下一页。
 */
export function useAssetPages(pageSize = 40) {
  const items = ref<ImageAsset[]>([])
  const total = ref(0)
  const query = ref('')
  const sort = ref<'newest' | 'name'>('newest')
  const loading = ref(false)
  const error = ref('')
  const hasMore = computed(() => items.value.length < total.value)
  let version = 0
  let searchTimer: number | undefined

  async function load(reset: boolean) {
    const current = ++version
    loading.value = true
    error.value = ''
    try {
      const page = await assetsApi.page({
        q: query.value.trim() || undefined,
        sort: sort.value === 'name' ? 'name' : undefined,
        limit: pageSize,
        offset: reset ? 0 : items.value.length,
      })
      if (current !== version) return
      if (reset) items.value = page.items
      else {
        const known = new Set(items.value.map((item) => item.id))
        items.value = [...items.value, ...page.items.filter((item) => !known.has(item.id))]
      }
      total.value = page.total
    } catch (reason) {
      if (current === version) error.value = errorMessage(reason, '加载图片失败')
    } finally {
      if (current === version) loading.value = false
    }
  }

  /** 刚上传的图片放到最前面，和服务端“最新在前”的顺序一致 */
  function prepend(uploaded: ImageAsset[]) {
    const known = new Set(items.value.map((item) => item.id))
    const added = uploaded.filter((item) => !known.has(item.id))
    items.value = [...added, ...items.value]
    total.value += added.length
  }

  function remove(ids: string[]) {
    const removed = new Set(ids)
    const before = items.value.length
    items.value = items.value.filter((item) => !removed.has(item.id))
    total.value = Math.max(0, total.value - (before - items.value.length))
  }

  watch(query, () => {
    window.clearTimeout(searchTimer)
    searchTimer = window.setTimeout(() => void load(true), 250)
  })
  watch(sort, () => void load(true))
  onMounted(() => void load(true))
  onUnmounted(() => window.clearTimeout(searchTimer))

  return {
    items, total, query, sort, loading, error, hasMore,
    refresh: () => load(true), loadMore: () => load(false), prepend, remove,
  }
}
