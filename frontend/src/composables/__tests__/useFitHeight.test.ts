import { mount } from '@vue/test-utils'
import { defineComponent, h, ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useFitHeight } from '../useFitHeight'

function setWindow(height: number, scrollY = 0) {
  Object.defineProperty(window, 'innerHeight', { value: height, configurable: true })
  Object.defineProperty(window, 'scrollY', { value: scrollY, configurable: true })
}
/** jsdom 没有布局，直接给元素指定位置 */
function placeAt(element: Element, top: number) {
  vi.spyOn(element, 'getBoundingClientRect').mockReturnValue({ top } as DOMRect)
}
function render(inScroller = false) {
  let fit!: ReturnType<typeof useFitHeight>
  const wrapper = mount(defineComponent({
    setup() {
      const container = ref<HTMLElement | null>(null)
      fit = useFitHeight(() => container.value?.querySelector<HTMLElement>('img'), container)
      return () => h('div', { class: 'scroller', style: inScroller ? 'overflow-y: auto' : undefined }, [
        h('section', { ref: container }, [h('img')]),
      ])
    },
  }), { attachTo: document.body })
  return { wrapper, fit, image: wrapper.find('img').element, scroller: wrapper.find('.scroller').element }
}

describe('fitting a result image into the visible area', () => {
  afterEach(() => { vi.restoreAllMocks(); document.body.innerHTML = '' })

  it('fills the rest of the window below where the image starts', () => {
    setWindow(900)
    const { fit, image } = render()
    placeAt(image, 544)
    fit.measure()
    expect(fit.height.value).toBe(900 - 544 - 24)

    // 页面滚动过也按滚动到顶部时计算
    setWindow(900, 100)
    placeAt(image, 444)
    fit.measure()
    expect(fit.height.value).toBe(332)

    setWindow(1000, 100)
    window.dispatchEvent(new Event('resize'))
    expect(fit.height.value).toBe(432)
  })

  it('shows the image at 60% of the screen when it cannot fit on the first screen', () => {
    setWindow(720)
    const { fit, image } = render()
    placeAt(image, 544)
    fit.measure()
    expect(fit.height.value).toBe(432)
  })

  it('measures inside a scrolling drawer instead of the window', () => {
    setWindow(900, 300)
    const { fit, image, scroller } = render(true)
    placeAt(scroller, 100)
    placeAt(image, 200)
    Object.defineProperty(scroller, 'clientHeight', { value: 600, configurable: true })
    Object.defineProperty(scroller, 'scrollTop', { value: 50, configurable: true })
    fit.measure()
    // 图片在抽屉内容里的位置：200 - 100 + 50 = 150
    expect(fit.height.value).toBe(600 - 150 - 24)
  })
})
