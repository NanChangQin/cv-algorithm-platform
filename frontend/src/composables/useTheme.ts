import { ref } from 'vue'

export type ThemePreference = 'system' | 'light' | 'dark'

const storageKey = 'cv-platform-theme'

function stored(): ThemePreference {
  try {
    const value = localStorage.getItem(storageKey)
    return value === 'light' || value === 'dark' ? value : 'system'
  } catch {
    return 'system'
  }
}

/** 外观偏好：跟随系统，或手动指定浅色/深色。index.html 里的脚本在首屏前就会应用它。 */
export const themePreference = ref<ThemePreference>(stored())

export function setThemePreference(preference: ThemePreference) {
  themePreference.value = preference
  const root = document.documentElement
  if (preference === 'system') root.removeAttribute('data-theme')
  else root.setAttribute('data-theme', preference)
  try {
    if (preference === 'system') localStorage.removeItem(storageKey)
    else localStorage.setItem(storageKey, preference)
  } catch {
    // 浏览器禁止存储时只在本次打开期间生效
  }
}

const order: ThemePreference[] = ['system', 'light', 'dark']
export const themeLabel: Record<ThemePreference, string> = { system: '跟随系统', light: '浅色', dark: '深色' }

export function nextTheme(preference: ThemePreference): ThemePreference {
  return order[(order.indexOf(preference) + 1) % order.length]!
}
