import { onBeforeUnmount, onMounted, ref, type Ref } from 'vue'

/** 最近的可滚动祖先（例如抽屉正文）；没有时按整个页面算 */
function scrollParent(element: HTMLElement): HTMLElement | null {
  for (let node = element.parentElement; node && node !== document.body; node = node.parentElement) {
    if (/(auto|scroll)/.test(getComputedStyle(node).overflowY)) return node
  }
  return null
}

/**
 * 让结果图整张留在可视区域内：按图片实际开始的位置算出还剩多少高度。
 * 上方的工具栏、标签换行后图片位置会变，所以窗口或所在区域大小变化时重新计算。
 * 在抽屉这类可滚动容器里按容器的可视区域算；位置一律按滚动到顶部时计算。
 * 剩余高度不到 min 时（矮屏、窄屏上下排列）整张放不进第一屏，反正要滚动，按可视高度的六成显示。
 */
export function useFitHeight(
  target: () => HTMLElement | null | undefined,
  container: Ref<HTMLElement | null>,
  { margin = 24, min = 200 } = {},
) {
  const height = ref<number>()

  function measure() {
    const element = target()
    if (!element || !container.value) return
    const scroller = scrollParent(container.value)
    const top = scroller
      ? element.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop
      : element.getBoundingClientRect().top + window.scrollY
    const visible = scroller ? scroller.clientHeight : window.innerHeight
    const remaining = visible - top - margin
    height.value = Math.floor(remaining >= min ? remaining : Math.max(min, visible * 0.6))
  }

  let observer: ResizeObserver | undefined
  onMounted(() => {
    measure()
    window.addEventListener('resize', measure)
    if (typeof ResizeObserver !== 'undefined' && container.value) {
      observer = new ResizeObserver(measure)
      observer.observe(container.value)
    }
  })
  onBeforeUnmount(() => {
    window.removeEventListener('resize', measure)
    observer?.disconnect()
  })
  return { height, measure }
}
