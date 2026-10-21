import { computed } from 'vue'
import { describe, expect, it } from 'vitest'

import { authenticationMode, sessionRole } from '../auth'

describe('session state', () => {
  it('updates navigation that was rendered before the session loaded', () => {
    sessionRole.clear()
    authenticationMode.clear()
    const isAdmin = computed(() => sessionRole.get() === 'admin')
    const showLogout = computed(() => authenticationMode.get())
    expect(isAdmin.value).toBe(false)
    expect(showLogout.value).toBe(false)

    sessionRole.set('admin')
    authenticationMode.set(true)

    expect(isAdmin.value).toBe(true)
    expect(showLogout.value).toBe(true)
    expect(localStorage.getItem('cv-platform-session-role')).toBe('admin')
  })
})
