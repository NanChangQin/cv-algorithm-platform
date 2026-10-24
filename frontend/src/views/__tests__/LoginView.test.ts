import { mount, flushPromises } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'
import LoginView from '../LoginView.vue'

const login = vi.hoisted(() => vi.fn().mockResolvedValue(undefined))
vi.mock('@/api/auth', () => ({ authApi: { login } }))

describe('login with an existing credential', () => {
  it('submits a six-character password and navigates after authentication', async () => {
    const router = createRouter({ history: createMemoryHistory(), routes: [
      { path: '/', component: { template: '<div />' } },
      { path: '/login', component: LoginView },
    ] })
    await router.push('/login')
    await router.isReady()
    const wrapper = mount(LoginView, { global: { plugins: [router] } })
    expect(wrapper.find('button').attributes('disabled')).toBeDefined()
    await wrapper.find('input[type="password"]').setValue('123456')
    expect(wrapper.find('button').attributes('disabled')).toBeUndefined()
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(login).toHaveBeenCalledWith('admin', '123456')
    expect(router.currentRoute.value.path).toBe('/')
    wrapper.unmount()
  })
})
