import { mount, flushPromises } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'
import AppShell from '../AppShell.vue'

vi.mock('@/api/auth', () => ({ authenticationMode: { get: () => false }, sessionRole: { get: () => 'admin' }, authApi: { logout: vi.fn() } }))
vi.mock('@/api/projects', () => ({ projectsApi: { list: vi.fn().mockResolvedValue([{ id: 'p1', name: '默认项目' }]) } }))

describe('responsive navigation', () => {
  it('closes the drawer after navigation and with Escape', async () => {
    const router = createRouter({ history: createMemoryHistory(), routes: [
      { path: '/', component: { template: '<div />' } },
      { path: '/:pathMatch(.*)*', component: { template: '<div />' } },
    ] })
    await router.push('/'); await router.isReady()
    const wrapper = mount(AppShell, { attachTo: document.body, global: { plugins: [router] } })
    await wrapper.find('[aria-label="打开导航菜单"]').trigger('click')
    await flushPromises()
    expect(wrapper.classes()).toContain('menu-open')
    expect(document.activeElement).toBe(wrapper.find('.brand').element)
    await wrapper.trigger('keydown', { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(wrapper.find('a[href="/system"]').element)
    await wrapper.trigger('keydown', { key: 'Tab' })
    expect(document.activeElement).toBe(wrapper.find('.brand').element)
    await wrapper.find('a[href="/tasks"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/tasks')
    expect(wrapper.classes()).not.toContain('menu-open')
    await wrapper.find('[aria-label="打开导航菜单"]').trigger('click')
    await wrapper.trigger('keydown', { key: 'Escape' })
    expect(wrapper.classes()).not.toContain('menu-open')
    wrapper.unmount()
  })
})
