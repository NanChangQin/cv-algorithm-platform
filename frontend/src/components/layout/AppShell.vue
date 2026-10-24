<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import { computed, nextTick, onMounted, ref, watch } from 'vue'

import { authenticationMode, authApi, sessionRole } from '@/api/auth'
import { projectContext } from '@/api/http'
import { projectsApi, type Project } from '@/api/projects'
import { nextTheme, setThemePreference, themeLabel, themePreference } from '@/composables/useTheme'
import AppIcon from './AppIcon.vue'

const router = useRouter()
const route = useRoute()
const menuOpen = ref(false)
const sidebarElement = ref<HTMLElement | null>(null)
const menuButton = ref<HTMLButtonElement | null>(null)
const projects = ref<Project[]>([])
const authenticationEnabled = computed(() => authenticationMode.get())
const navigation = [
  { label: '训练中心', to: '/training', icon: 'training', group: '模型训练', roles: ['admin', 'developer'] },
  { label: '训练任务', to: '/training/tasks', icon: 'tasks', group: '模型训练', roles: ['admin', 'developer'] },
  { label: '评测中心', to: '/evaluations', icon: 'chart', group: '模型训练' },
  { label: '模型导出', to: '/exports', icon: 'deploy', group: '模型训练', roles: ['admin', 'developer'] },
  { label: '推理中心', to: '/', icon: 'play', group: '模型推理' },
  { label: '推理任务', to: '/inference/tasks', icon: 'tasks', group: '模型推理' },
  { label: '结果对比', to: '/compare', icon: 'compare', group: '模型推理' },
  { label: '模型部署', to: '/deployments', icon: 'deploy', group: '模型推理', roles: ['admin', 'developer'] },
  { label: '训练数据集', to: '/training/datasets', icon: 'folder', group: '资源库', roles: ['admin', 'developer'] },
  { label: '问题样本', to: '/training/reviews', icon: 'image', group: '资源库', roles: ['admin', 'developer'] },
  { label: '图片库', to: '/assets', icon: 'image', group: '资源库' },
  { label: '算法库', to: '/algorithms', icon: 'layers', group: '资源库' },
  { label: '项目成员', to: '/projects', icon: 'folder', group: '平台管理' },
  { label: '运行实例', to: '/instances', icon: 'server', group: '平台管理', roles: ['admin'] },
  { label: '高级编排', to: '/advanced', icon: 'workflow', group: '平台管理', roles: ['admin'] },
  { label: '用户角色', to: '/users', icon: 'users', group: '平台管理', roles: ['admin'] },
  { label: '系统管理', to: '/system', icon: 'settings', group: '平台管理', roles: ['admin'] },
]
const groups = ['模型训练', '模型推理', '资源库', '平台管理']
const visibleNavigation = computed(() => navigation.filter((item) => !item.roles || item.roles.includes(sessionRole.get() ?? 'user')))
const visibleGroups = computed(() => groups.filter((group) => visibleNavigation.value.some((item) => item.group === group)))
const activeNavigation = computed(() => {
  if (route.path.startsWith('/tasks')) return navigation.find((item) => item.to === '/inference/tasks')
  if (route.name === 'training-run' || route.name === 'training-report') return navigation.find((item) => item.to === '/training/tasks')
  const matches = navigation.filter((item) => item.to === route.path || (item.to !== '/' && route.path.startsWith(`${item.to}/`)))
  return matches.sort((a, b) => b.to.length - a.to.length)[0]
})
const currentPage = computed(() => {
  if (route.name === 'training-run') return '训练任务详情'
  if (route.name === 'training-report') return '训练报告'
  if (route.name === 'training-create') return route.query.operation === 'val' || route.query.validate ? '独立验证' : route.query.resume ? '断点续训' : '新建训练'
  if (route.name === 'task-detail') return '任务详情'
  return activeNavigation.value?.label ?? '推理中心'
})
const currentProject = computed(() => projects.value.find((project) => project.id === projectContext.get()) ?? projects.value[0])
const themeIcon = computed(() => ({ system: 'monitor', light: 'sun', dark: 'moon' })[themePreference.value])

watch(() => route.fullPath, () => { menuOpen.value = false })
watch(menuOpen, async (open) => {
  await nextTick()
  if (open !== menuOpen.value) return
  if (open) sidebarElement.value?.querySelector<HTMLAnchorElement>('a')?.focus()
  else menuButton.value?.focus()
})
onMounted(async () => {
  try { projects.value = await projectsApi.list() } catch { /* 读不到项目时不显示切换器 */ }
})

function handleMenuTab(event: KeyboardEvent) {
  if (!menuOpen.value) return
  const items = sidebarElement.value?.querySelectorAll<HTMLElement>('a, button:not([disabled]), select')
  const first = items?.[0]
  const last = items?.[items.length - 1]
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
}
function switchProject(id: string) {
  if (id === currentProject.value?.id) return
  projectContext.set(id)
  // 项目隔离所有数据，整页重新加载最稳妥
  window.location.assign('/')
}
async function logout() {
  await authApi.logout()
  await router.push('/login')
}
</script>

<template>
  <div class="app-shell" :class="{ 'menu-open': menuOpen }" @keydown.esc="menuOpen = false" @keydown.tab="handleMenuTab">
    <button v-if="menuOpen" class="nav-backdrop" aria-label="关闭导航菜单" @click="menuOpen = false" />
    <aside id="app-navigation" ref="sidebarElement" class="sidebar" :class="{ 'is-open': menuOpen }">
      <div class="brand-row">
        <RouterLink class="brand" to="/" @click="menuOpen = false">
          <span class="brand-mark">CV</span>
          <span><strong>视觉算法平台</strong><small>Algorithm Studio</small></span>
        </RouterLink>
        <button class="icon-button mobile-nav-close" type="button" aria-label="关闭菜单" @click="menuOpen = false"><AppIcon name="close" /></button>
      </div>

      <nav aria-label="主导航">
        <div v-for="group in visibleGroups" :key="group" class="nav-group">
          <span class="nav-group-label">{{ group }}</span>
          <RouterLink
            v-for="item in visibleNavigation.filter((entry) => entry.group === group)"
            :key="item.to"
            :to="item.to"
            class="nav-item"
            active-class="route-match"
            exact-active-class="route-exact-match"
            :class="{ 'router-link-active': activeNavigation?.to === item.to }"
            @click="menuOpen = false"
          >
            <AppIcon :name="item.icon" />{{ item.label }}
          </RouterLink>
        </div>
      </nav>

      <div class="sidebar-footer">
        <AppIcon name="folder" />
        <div>
          <small>当前项目</small>
          <select
            v-if="projects.length > 1"
            :value="currentProject?.id"
            aria-label="切换项目"
            @change="switchProject(($event.target as HTMLSelectElement).value)"
          >
            <option v-for="project in projects" :key="project.id" :value="project.id">{{ project.name }}</option>
          </select>
          <strong v-else>{{ currentProject?.name ?? '默认项目' }}</strong>
        </div>
      </div>
    </aside>

    <main class="main-content">
      <header class="topbar">
        <div class="topbar-context">
          <button ref="menuButton" class="icon-button mobile-menu-button" type="button" aria-label="打开导航菜单" :aria-expanded="menuOpen" aria-controls="app-navigation" @click="menuOpen = !menuOpen"><AppIcon name="menu" /></button>
          <strong>{{ currentPage }}</strong>
        </div>
        <div class="topbar-actions">
          <RouterLink v-if="route.path !== '/' && !route.path.startsWith('/training')" class="secondary-button" to="/"><AppIcon name="play" />新建推理</RouterLink>
          <button
            type="button"
            class="icon-button"
            :title="`外观：${themeLabel[themePreference]}（点击切换为${themeLabel[nextTheme(themePreference)]}）`"
            :aria-label="`外观：${themeLabel[themePreference]}，点击切换为${themeLabel[nextTheme(themePreference)]}`"
            @click="setThemePreference(nextTheme(themePreference))"
          ><AppIcon :name="themeIcon" /></button>
          <button v-if="authenticationEnabled" class="secondary-button" @click="logout">退出</button>
        </div>
      </header>
      <div class="page-content"><slot /></div>
    </main>
  </div>
</template>
