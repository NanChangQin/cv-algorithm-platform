<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { authApi } from '@/api/auth'
import { errorMessage } from '@/utils/errors'

const router = useRouter()
const route = useRoute()
const username = ref('admin')
const password = ref('')
const submitting = ref(false)
const error = ref('')

async function login() {
  submitting.value = true
  error.value = ''
  try {
    await authApi.login(username.value, password.value)
    // 回到登录前想访问的页面（只接受站内路径）
    const redirect = route.query.redirect
    await router.push(typeof redirect === 'string' && redirect.startsWith('/') && !redirect.startsWith('//') ? redirect : '/')
  } catch (reason) {
    error.value = errorMessage(reason, '登录失败，请检查用户名和密码')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <main class="login-page">
    <form class="login-panel" @submit.prevent="login">
      <span class="brand-mark">CV</span>
      <h1>登录视觉算法平台</h1>
      <p>使用管理员为你创建的账号登录。</p>
      <div v-if="error" class="alert">{{ error }}</div>
      <label>用户名<input v-model="username" autocomplete="username" /></label>
      <label>密码<input v-model="password" type="password" autocomplete="current-password" /></label>
      <small v-if="password && password.length < 12" class="muted-copy">密码至少 12 位</small>
      <button class="primary-button" :disabled="submitting || password.length < 12">
        {{ submitting ? '正在验证…' : '登录' }}
      </button>
    </form>
  </main>
</template>
