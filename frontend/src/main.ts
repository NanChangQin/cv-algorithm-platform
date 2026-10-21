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

app.use(router).mount('#app')
