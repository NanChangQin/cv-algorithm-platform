import { ref } from 'vue'

import { projectContext, request, sessionToken } from './http'

export interface Session {
  user_id: string
  username: string
  role: string
  default_project_id: string | null
  expires_at: string
  authentication_enabled: boolean
}
export interface User {
  id: string; username: string; role: 'admin' | 'developer' | 'user'; enabled: boolean; created_at: string
}

export const authApi = {
  login: async (username: string, password: string) => {
    const session = await request<Session>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    })
    applySession(session)
    return session
  },
  me: async () => {
    const session = await request<Session>('/auth/me')
    applySession(session)
    return session
  },
  users: () => request<User[]>('/users'),
  createUser: (payload: { username: string; password: string; role: User['role'] }) =>
    request<User>('/users', { method: 'POST', body: JSON.stringify(payload) }),
  updateUser: (id: string, payload: { role?: User['role']; enabled?: boolean }) =>
    request<User>(`/users/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  logout: async () => {
    await request<void>('/auth/logout', { method: 'POST' })
    sessionToken.clear()
    sessionRole.clear()
    authenticationMode.clear()
    projectContext.clear()
  },
}

function applySession(session: Session) {
  sessionToken.set()
  sessionRole.set(session.role)
  authenticationMode.set(session.authentication_enabled)
  if (session.default_project_id) projectContext.set(session.default_project_id)
}

// 角色与登录模式保存在响应式状态中并同步到 localStorage：
// 首次访问时侧栏先于 /auth/me 渲染，若只读 localStorage，管理员菜单要刷新页面才会出现。
const sessionRoleKey = 'cv-platform-session-role'
const roleState = ref(localStorage.getItem(sessionRoleKey))
export const sessionRole = {
  get: () => roleState.value,
  set: (role: string) => { roleState.value = role; localStorage.setItem(sessionRoleKey, role) },
  clear: () => { roleState.value = null; localStorage.removeItem(sessionRoleKey) },
}

const authenticationModeKey = 'cv-platform-authentication-enabled'
const authenticationState = ref(localStorage.getItem(authenticationModeKey) === 'true')
export const authenticationMode = {
  get: () => authenticationState.value,
  set: (enabled: boolean) => { authenticationState.value = enabled; localStorage.setItem(authenticationModeKey, String(enabled)) },
  clear: () => { authenticationState.value = false; localStorage.removeItem(authenticationModeKey) },
}
