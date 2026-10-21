import { createApp } from 'vue'

import App from './App.vue'
import { ApiError } from './api/http'
import { toast } from './composables/useToast'
import { router } from './router'
import { errorMessage } from './utils/errors'
import './styles/main.css'

const app = createApp(App)

// 兜底：页面里没有单独处理的失败操作也要让用户看到原因，而不是“点了没反应”
function reportUnhandled(reason: unknown) {
  if (reason instanceof ApiError && reason.status === 401) return
  console.error(reason)
  toast.error(errorMessage(reason, '操作没有完成，请稍后重试'))
}
app.config.errorHandler = reportUnhandled
window.addEventListener('unhandledrejection', (event) => reportUnhandled(event.reason))

// 发版后旧页面引用的分包文件已被替换，切换页面会加载失败；刷新一次拿到新版本。
// 10 秒内不重复刷新，避免文件确实缺失时陷入循环。
window.addEventListener('vite:preloadError', (event) => {
  const key = 'cv-platform:reloaded-for-update'
  try {
    if (Date.now() - Number(sessionStorage.getItem(key) ?? 0) < 10_000) return
    sessionStorage.setItem(key, String(Date.now()))
  } catch {
    return
  }
  event.preventDefault()
  window.location.reload()
})

app.use(router).mount('#app')
