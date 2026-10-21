<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { authApi, type User } from '@/api/auth'
import { toast } from '@/composables/useToast'
import { errorMessage } from '@/utils/errors'
import { formatDateTime, platformRoleLabel } from '@/utils/presentation'

const users = ref<User[]>([])
const drafts = ref<Record<string, { role: User['role']; enabled: boolean }>>({})
const username = ref('')
const password = ref('')
const role = ref<User['role']>('user')
const error = ref('')
const busy = ref(false)
const currentUser = ref('')
const usernameValid = computed(() => /^[a-zA-Z0-9_.-]{3,128}$/.test(username.value))
const roleHints: Record<User['role'], string> = {
  admin: '管理所有功能，包括用户、节点和系统设置', developer: '可以导入、构建算法', user: '使用算法推理、查看结果',
}

async function refresh() {
  try {
    users.value = await authApi.users()
    drafts.value = Object.fromEntries(users.value.map((user) => [user.id, { role: user.role, enabled: user.enabled }]))
  } catch (reason) { error.value = errorMessage(reason, '用户加载失败') }
}
function changed(user: User) {
  const draft = drafts.value[user.id]
  return Boolean(draft && (draft.role !== user.role || draft.enabled !== user.enabled))
}
async function createUser() {
  busy.value = true
  try {
    const user = await authApi.createUser({ username: username.value.trim(), password: password.value, role: role.value })
    username.value = ''; password.value = ''
    toast.success(`已创建 ${user.username}（${platformRoleLabel[user.role]}）`)
    await refresh()
  } catch (reason) { toast.error(errorMessage(reason, '创建用户失败')) } finally { busy.value = false }
}
async function save(user: User) {
  busy.value = true
  try {
    await authApi.updateUser(user.id, drafts.value[user.id]!)
    toast.success(`已保存 ${user.username}`)
    await refresh()
  } catch (reason) { toast.error(errorMessage(reason, '保存失败')) } finally { busy.value = false }
}
onMounted(async () => {
  await refresh()
  try { currentUser.value = (await authApi.me()).username } catch { /* 仅用于禁止停用自己 */ }
})
</script>

<template>
  <header class="page-heading"><h1>用户与角色</h1><p>管理员创建账号并设置平台角色；项目内的访问权限在“项目成员”中设置。</p></header>
  <p v-if="error" class="alert" role="alert">{{ error }}</p>
  <section class="section-block">
    <header class="section-heading"><div><h2>创建用户</h2></div></header>
    <form class="user-create" @submit.prevent="createUser">
      <label>用户名<input v-model="username" required autocomplete="off" placeholder="3–128 位字母、数字、. _ -" :aria-invalid="Boolean(username) && !usernameValid" /></label>
      <label>初始密码<input v-model="password" type="password" required autocomplete="new-password" placeholder="至少 12 位" /></label>
      <label>平台角色<select v-model="role"><option v-for="(label, value) in platformRoleLabel" :key="value" :value="value">{{ label }}</option></select></label>
      <button class="primary-button compact-button" :disabled="busy || !usernameValid || password.length < 12">创建</button>
    </form>
    <small class="muted-copy">{{ roleHints[role] }}</small>
  </section>
  <section class="section-block">
    <header class="section-heading"><div><h2>全部用户</h2><span class="count-badge">{{ users.length }}</span></div></header>
    <div class="user-table">
      <div v-for="user in users" :key="user.id" class="user-row">
        <span><strong>{{ user.username }}</strong><small v-if="user.username === currentUser">（当前账号）</small><small>{{ formatDateTime(user.created_at) }} 创建</small></span>
        <select v-if="drafts[user.id]" v-model="drafts[user.id]!.role" :aria-label="`${user.username} 的角色`"><option v-for="(label, value) in platformRoleLabel" :key="value" :value="value">{{ label }}</option></select>
        <label v-if="drafts[user.id]" class="check-control"><input v-model="drafts[user.id]!.enabled" type="checkbox" role="switch" :disabled="user.username === currentUser" />{{ drafts[user.id]!.enabled ? '已启用' : '已停用' }}</label>
        <button class="secondary-button" :disabled="busy || !changed(user)" @click="save(user)">保存</button>
      </div>
    </div>
  </section>
</template>
