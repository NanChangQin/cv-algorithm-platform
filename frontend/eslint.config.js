import js from '@eslint/js'
import pluginVue from 'eslint-plugin-vue'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['dist/**', 'coverage/**'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  ...pluginVue.configs['flat/recommended'],
  {
    // <script lang="ts"> 需要交给 TypeScript 解析器，否则 import type 等语法会报解析错误
    files: ['**/*.vue'],
    languageOptions: { parserOptions: { parser: tseslint.parser } },
  },
  {
    files: ['**/*.{ts,vue}'],
    rules: {
      // 未定义变量由 vue-tsc 检查；no-undef 不认识 window、document 等浏览器全局（typescript-eslint 官方建议关闭）
      'no-undef': 'off',
      'vue/multi-word-component-names': 'off',
      // 项目沿用紧凑的单行模板写法，以下纯格式规则与之冲突
      'vue/max-attributes-per-line': 'off',
      'vue/singleline-html-element-content-newline': 'off',
      'vue/multiline-html-element-content-newline': 'off',
      'vue/html-self-closing': 'off',
    },
  },
)

