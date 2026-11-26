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
    { path: '/inference/tasks', name: 'inference-tasks', component: () => import('@/views/InferenceTasksView.vue') },
    { path: '/inference/tasks/:id', name: 'inference-session', component: () => import('@/views/InferenceSessionView.vue') },
    { path: '/', name: 'workspace', component: () => import('@/views/InferenceCenterView.vue') },
    { path: '/algorithms', name: 'algorithms', component: () => import('@/views/AlgorithmsView.vue') },
    { path: '/assets', name: 'assets', component: () => import('@/views/AssetsView.vue') },
    { path: '/instances', name: 'instances', component: () => import('@/views/InstancesView.vue') },
    { path: '/tasks', name: 'tasks', component: () => import('@/views/TasksView.vue') },
    { path: '/tasks/:id', name: 'task-detail', component: () => import('@/views/TaskDetailView.vue') },
    { path: '/compare/:id?', name: 'compare', component: () => import('@/views/CompareView.vue') },
    { path: '/evaluations', name: 'evaluations', component: () => import('@/views/EvaluationsView.vue') },
    { path: '/evaluations/:id', name: 'evaluation-report', component: () => import('@/views/EvaluationReportView.vue') },
    { path: '/exports/:id?', name: 'exports', component: () => import('@/views/ExportsView.vue'), meta: { roles: ['admin', 'developer'] } },
    { path: '/training', name: 'training', component: () => import('@/views/TrainingView.vue'), beforeEnter: (to) => { if (['dataset', 'resume', 'finetune', 'model', 'validate', 'operation'].some((key) => to.query[key])) return { path: '/training/new', query: to.query, replace: true } }, meta: { roles: ['admin', 'developer'] } },
    { path: '/training/reviews', name: 'sample-reviews', component: () => import('@/views/SampleReviewsView.vue'), meta: { roles: ['admin', 'developer'] } },
    { path: '/training/compare', name: 'training-compare', component: () => import('@/views/TrainingCompareView.vue'), meta: { roles: ['admin', 'developer'] } },
    { path: '/training/tasks', name: 'training-tasks', component: () => import('@/views/TrainingTasksView.vue'), meta: { roles: ['admin', 'developer'] } },
    { path: '/training/new', name: 'training-create', component: () => import('@/views/TrainingCreateView.vue'), meta: { roles: ['admin', 'developer'] } },
    { path: '/training/datasets', name: 'training-datasets', component: () => import('@/views/TrainingDatasetsView.vue'), meta: { roles: ['admin', 'developer'] } },
    { path: '/training/datasets/:id', name: 'training-dataset', component: () => import('@/views/DatasetView.vue'), meta: { roles: ['admin', 'developer'] } },
    { path: '/training/:id/report', name: 'training-report', component: () => import('@/views/TrainingReportView.vue'), meta: { roles: ['admin', 'developer'] } },
    { path: '/training/:id', name: 'training-run', component: () => import('@/views/TrainingRunView.vue'), meta: { roles: ['admin', 'developer'] } },
    { path: '/deployments', name: 'deployments', component: () => import('@/views/DeploymentsView.vue'), meta: { roles: ['admin', 'developer'] } },
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
