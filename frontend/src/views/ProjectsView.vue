<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { projectContext } from '@/api/http'
import { projectsApi, type Project, type ProjectMember } from '@/api/projects'
import { toast } from '@/composables/useToast'
import { errorMessage } from '@/utils/errors'
import { formatDateTime, projectRoleLabel } from '@/utils/presentation'

const projects = ref<Project[]>([])
const members = ref<ProjectMember[]>([])
const currentId = ref(projectContext.get() ?? '')
const viewingId = ref(currentId.value)
const name = ref('')
const description = ref('')
const memberUsername = ref('')
const memberRole = ref<ProjectMember['role']>('viewer')
const error = ref('')
const busy = ref(false)
const viewing = computed(() => projects.value.find((item) => item.id === viewingId.value))
const roleHints: Record<ProjectMember['role'], string> = {
  owner: '可管理成员和全部资源', editor: '可上传图片、提交任务、导入算法', viewer: '只能查看和下载结果',
}

async function refresh() {
  try {
    projects.value = await projectsApi.list()
    if (!projects.value.some((item) => item.id === viewingId.value)) viewingId.value = projects.value[0]?.id ?? ''
    if (!currentId.value && projects.value[0]) currentId.value = projects.value[0].id
    await loadMembers()
  } catch (reason) { error.value = errorMessage(reason, '项目加载失败') }
}
async function loadMembers() {
  if (!viewingId.value) { members.value = []; return }
  try { members.value = await projectsApi.members(viewingId.value) } catch (reason) { members.value = []; toast.error(errorMessage(reason, '成员加载失败')) }
}
async function view(project: Project) { viewingId.value = project.id; await loadMembers() }
async function createProject() {
  busy.value = true
  try {
    const project = await projectsApi.create({ name: name.value.trim(), description: description.value.trim() })
    name.value = ''; description.value = ''
    toast.success(`已创建项目 ${project.name}`)
    viewingId.value = project.id
    await refresh()
  } catch (reason) { toast.error(errorMessage(reason, '创建项目失败')) } finally { busy.value = false }
}
async function addMember() {
  busy.value = true
  try {
    const member = await projectsApi.addMember(viewingId.value, { username: memberUsername.value.trim(), role: memberRole.value })
    memberUsername.value = ''
    toast.success(`${member.username} 已成为${projectRoleLabel[member.role]}`)
    await loadMembers()
  } catch (reason) { toast.error(errorMessage(reason, '添加成员失败')) } finally { busy.value = false }
}
function activate(project: Project) {
  projectContext.set(project.id)
  window.location.assign('/')
}
onMounted(refresh)
</script>

<template>
  <header class="page-heading"><h1>项目与成员</h1><p>项目之间隔离算法、图片、任务、媒体源和工作流；成员按所有者、编辑者、访客授权。</p></header>
  <p v-if="error" class="alert" role="alert">{{ error }}</p>
  <div class="project-grid">
    <section class="section-block">
      <header class="section-heading"><div><h2>我的项目</h2><span class="count-badge">{{ projects.length }}</span></div></header>
      <ul class="plain-list selectable">
        <li v-for="item in projects" :key="item.id" :class="{ active: item.id === viewingId }">
          <button type="button" class="plain-list-main" @click="view(item)">
            <strong>{{ item.name }}<span v-if="item.id === currentId" class="runtime-status status-healthy">当前项目</span></strong>
            <small>{{ item.description || '无描述' }} · 我是{{ projectRoleLabel[item.role] }}</small>
          </button>
          <button v-if="item.id !== currentId" type="button" class="secondary-button compact" @click="activate(item)">切换</button>
        </li>
      </ul>
      <form class="stacked-form new-project" @submit.prevent="createProject">
        <h3>新建项目</h3>
        <label>项目名称<input v-model="name" required maxlength="128" /></label>
        <label>说明<textarea v-model="description" maxlength="1000" placeholder="可选" /></label>
        <button class="primary-button" :disabled="busy || !name.trim()">创建项目</button>
      </form>
    </section>
    <section class="section-block">
      <header class="section-heading"><div><h2>{{ viewing?.name ?? '项目' }} 的成员</h2><span class="count-badge">{{ members.length }}</span></div></header>
      <ul class="plain-list">
        <li v-for="item in members" :key="item.user_id"><span><strong>{{ item.username }}</strong><small>{{ formatDateTime(item.joined_at) }} 加入</small></span><span class="runtime-status">{{ projectRoleLabel[item.role] }}</span></li>
      </ul>
      <form v-if="viewing?.role === 'owner'" class="stacked-form" @submit.prevent="addMember">
        <h3>添加或调整成员</h3>
        <label>用户名<input v-model="memberUsername" required placeholder="已在“用户角色”中创建的用户名" /></label>
        <label>项目角色<select v-model="memberRole"><option v-for="(label, value) in projectRoleLabel" :key="value" :value="value">{{ label }}</option></select><small>{{ roleHints[memberRole] }}</small></label>
        <button class="primary-button" :disabled="busy || !memberUsername.trim()">保存成员</button>
      </form>
      <p v-else-if="viewing" class="muted-copy">只有项目所有者可以调整成员。</p>
    </section>
  </div>
</template>
