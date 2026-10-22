import { createRouter, createWebHistory } from 'vue-router'
import { sessionToken } from '@/api/http'
import { authApi, sessionRole } from '@/api/auth'

export const router = createRouter({
  history: createWebHistory(),
  scrollBehavior(to, from, savedPosition) {
    if (savedPosition) return savedPosition
    return to.path !== from.path ? { top: 0 } : undefined
  },
  routes: [
    { path: '/login', name: 'login', component: () => import('@/views/LoginView.vue'), meta: { public: true } },
    { path: '/', name: 'workspace', component: () => import('@/views/WorkspaceView.vue') },
    { path: '/algorithms', name: 'algorithms', component: () => import('@/views/AlgorithmsView.vue') },
    { path: '/assets', name: 'assets', component: () => import('@/views/AssetsView.vue') },
    { path: '/instances', name: 'instances', component: () => import('@/views/InstancesView.vue') },
    { path: '/tasks', name: 'tasks', component: () => import('@/views/TasksView.vue') },
    { path: '/tasks/:id', name: 'task-detail', component: () => import('@/views/TaskDetailView.vue') },
    { path: '/compare/:id?', name: 'compare', component: () => import('@/views/CompareView.vue') },
    { path: '/evaluations', name: 'evaluations', component: () => import('@/views/EvaluationsView.vue') },
    { path: '/evaluations/:id', name: 'evaluation-report', component: () => import('@/views/EvaluationReportView.vue') },
    { path: '/training', name: 'training', component: () => import('@/views/TrainingView.vue'), meta: { roles: ['admin', 'developer'] } },
    { path: '/training/:id', name: 'training-run', component: () => import('@/views/TrainingRunView.vue'), meta: { roles: ['admin', 'developer'] } },
    { path: '/system', name: 'system', component: () => import('@/views/SystemView.vue'), meta: { roles: ['admin'] } },
    { path: '/advanced', name: 'advanced', component: () => import('@/views/AdvancedView.vue'), meta: { roles: ['admin'] } },
    { path: '/projects', name: 'projects', component: () => import('@/views/ProjectsView.vue') },
    { path: '/users', name: 'users', component: () => import('@/views/UsersView.vue'), meta: { roles: ['admin'] } },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

let authenticationInitialized = false

async function initializeAuthentication(): Promise<boolean> {
  if (authenticationInitialized) return Boolean(sessionToken.get())
  authenticationInitialized = true
  try {
    await authApi.me()
    return true
  } catch {
    sessionToken.clear()
    sessionRole.clear()
    return false
  }
}

router.beforeEach(async (to) => {
  const authenticated = await initializeAuthentication()
  if (!to.meta.public && !authenticated) return { name: 'login', query: { redirect: to.fullPath } }
  if (to.name === 'login' && authenticated) return { name: 'workspace' }
  const roles = to.meta.roles as string[] | undefined
  if (roles && !roles.includes(sessionRole.get() ?? 'user')) return { name: 'workspace' }
})
