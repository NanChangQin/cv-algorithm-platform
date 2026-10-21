<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import { computed, nextTick, ref, watch } from 'vue'

import { authenticationMode, authApi, sessionRole } from '@/api/auth'
import AppIcon from './AppIcon.vue'

const router = useRouter()
const route = useRoute()
const menuOpen = ref(false)
const sidebarElement = ref<HTMLElement | null>(null)
const menuButton = ref<HTMLButtonElement | null>(null)
const authenticationEnabled = computed(() => authenticationMode.get())
const navigation = [
  { label: '工作台', to: '/', icon: 'home', group: '工作空间' },
  { label: '算法中心', to: '/algorithms', icon: 'layers', group: '工作空间' },
  { label: '资源中心', to: '/assets', icon: 'image', group: '工作空间' },
  { label: '任务中心', to: '/tasks', icon: 'tasks', group: '工作空间' },
  { label: '结果对比', to: '/compare', icon: 'compare', group: '工作空间' },
  { label: '运行实例', to: '/instances', icon: 'server', group: '管理与运维', roles: ['admin'] },
  { label: '系统管理', to: '/system', icon: 'settings', group: '管理与运维', roles: ['admin'] },
  { label: '高级编排', to: '/advanced', icon: 'workflow', group: '管理与运维', roles: ['admin'] },
  { label: '项目成员', to: '/projects', icon: 'users', group: '管理与运维' },
  { label: '用户角色', to: '/users', icon: 'users', group: '管理与运维', roles: ['admin'] },
]
const visibleNavigation = computed(() => navigation.filter((item) => !item.roles || item.roles.includes(sessionRole.get() ?? 'user')))
const currentPage = computed(() => {
  if (route.name === 'task-detail') return '任务详情'
  // /compare/<id> 等子路径归到对应的导航项；首页只做精确匹配
  const matches = navigation.filter((item) => item.to === route.path || (item.to !== '/' && route.path.startsWith(`${item.to}/`)))
  return matches.sort((a, b) => b.to.length - a.to.length)[0]?.label ?? '工作空间'
})
watch(() => route.fullPath, () => { menuOpen.value = false })
watch(menuOpen, async (open) => {
  await nextTick()
  if (open !== menuOpen.value) return
  if (open) sidebarElement.value?.querySelector<HTMLAnchorElement>('a')?.focus()
  else menuButton.value?.focus()
})
function handleMenuTab(event: KeyboardEvent) {
  if (!menuOpen.value) return
  const items = sidebarElement.value?.querySelectorAll<HTMLElement>('a, button:not([disabled])')
  const first = items?.[0]
  const last = items?.[items.length - 1]
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
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
        <div v-for="group in ['工作空间', '管理与运维']" :key="group" class="nav-group">
          <span class="nav-group-label">{{ group }}</span>
          <RouterLink v-for="item in visibleNavigation.filter((entry) => entry.group === group)" :key="item.to" :to="item.to" class="nav-item" @click="menuOpen = false">
            <AppIcon :name="item.icon" />{{ item.label }}
          </RouterLink>
        </div>
      </nav>

      <div class="sidebar-footer">
        <AppIcon name="server" />
        <div><strong>本地与远程 GPU</strong><small>统一任务 · 标准结果</small></div>
      </div>
    </aside>

    <main class="main-content">
      <header class="topbar">
        <div class="topbar-context">
          <button ref="menuButton" class="icon-button mobile-menu-button" type="button" aria-label="打开导航菜单" :aria-expanded="menuOpen" aria-controls="app-navigation" @click="menuOpen = !menuOpen"><AppIcon name="menu" /></button>
          <span class="topbar-workspace">工作空间 <span>/</span></span>
          <strong>{{ currentPage }}</strong>
        </div>
        <div class="topbar-actions">
          <RouterLink v-if="route.path !== '/'" class="secondary-button" to="/">新建推理</RouterLink>
          <button v-if="authenticationEnabled" class="secondary-button" @click="logout">退出</button>
        </div>
      </header>
      <div class="page-content"><slot /></div>
    </main>
  </div>
</template>
