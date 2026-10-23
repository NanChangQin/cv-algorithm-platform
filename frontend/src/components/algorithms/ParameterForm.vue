<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { parseDocument, stringify } from 'yaml'
import type { JsonSchema, ParameterSpec } from '@/types/algorithm'
import { isJsonValue, mergeParameters, numericBounds, parameterIssue, schemaForParameter, type ParameterValue, type ParameterValues } from '@/utils/parameters'

const props = defineProps<{ parameters: Record<string, ParameterSpec>; modelValue: ParameterValues; defaults?: ParameterValues }>()
const emit = defineEmits<{ 'update:modelValue': [value: ParameterValues]; validity: [issue: string] }>()
const view = ref<'common' | 'all' | 'code'>('common')
const query = ref('')
const group = ref('')
const changedOnly = ref(false)
const format = ref<'yaml' | 'json'>('yaml')
const draft = ref('')
const dirty = ref(false)
const draftError = ref('')
const fieldErrors = ref<Record<string, string>>({})
const complexDrafts = ref<Record<string, string>>({})
const entries = computed(() => Object.entries(props.parameters))
const defaults = computed(() => mergeParameters(props.parameters, props.defaults))
const groups = computed(() => [...new Set(entries.value.map(([, spec]) => spec.group).filter(Boolean))])
const hasCommon = computed(() => entries.value.some(([, spec]) => spec.common))
const filtered = computed(() => entries.value.filter(([name, spec]) => {
  if (view.value === 'common' && hasCommon.value && !spec.common && !query.value) return false
  if (group.value && spec.group !== group.value) return false
  if (changedOnly.value && JSON.stringify(value(name)) === JSON.stringify(defaults.value[name])) return false
  return `${name} ${spec.title ?? ''} ${spec.description ?? ''}`.toLowerCase().includes(query.value.trim().toLowerCase())
}))
const issue = computed(() => draftError.value || Object.values(fieldErrors.value).find(Boolean) || (dirty.value ? '请先应用专家配置' : '') || parameterIssue(props.parameters, props.modelValue))
watch(issue, (value) => emit('validity', value), { immediate: true })
watch(() => props.parameters, () => { fieldErrors.value = {}; complexDrafts.value = {}; dirty.value = false; draftError.value = '' })
watch(() => props.modelValue, () => { if (!dirty.value) draft.value = serialize(props.modelValue) }, { deep: true, immediate: true })

function value(name: string): ParameterValue { return Object.prototype.hasOwnProperty.call(props.modelValue, name) ? props.modelValue[name]! : defaults.value[name]! }
function update(name: string, next: ParameterValue) {
  delete fieldErrors.value[name]
  delete complexDrafts.value[name]
  emit('update:modelValue', { ...props.modelValue, [name]: next })
}
function schema(spec: ParameterSpec): JsonSchema { return schemaForParameter(spec) }
function controlType(spec: ParameterSpec) {
  const item = schema(spec)
  if (Array.isArray(item.enum)) return 'enum'
  if (typeof item.type === 'string' && ['number', 'integer', 'boolean', 'string'].includes(item.type)) return item.type
  const variants = item.anyOf as JsonSchema[] | undefined
  if (variants?.every((variant) => variant.type === 'number' || variant.type === 'integer' || typeof variant.const === 'number')) return 'number'
  return 'json'
}
function numberInput(event: Event) { const raw = (event.target as HTMLInputElement).value; return raw === '' ? Number.NaN : Number(raw) }
function bounds(spec: ParameterSpec) { return { ...numericBounds(spec), step: schema(spec).anyOf ? 'any' : numericBounds(spec).step } }
function parseComplex(name: string, text: string) {
  complexDrafts.value[name] = text
  try {
    const parsed: unknown = JSON.parse(text)
    if (!isJsonValue(parsed)) throw new Error('请输入有效 JSON')
    const error = parameterIssue({ [name]: props.parameters[name]! }, { [name]: parsed })
    if (error) throw new Error(error)
    delete fieldErrors.value[name]
    emit('update:modelValue', { ...props.modelValue, [name]: parsed })
  } catch (error) { fieldErrors.value[name] = error instanceof Error ? error.message : '请输入有效 JSON' }
}
function serialize(values: ParameterValues) { return format.value === 'json' ? JSON.stringify(values, null, 2) : stringify(values) }
function switchView(next: 'common' | 'all' | 'code') { view.value = next; if (next === 'code' && !dirty.value) draft.value = serialize(props.modelValue) }
function changeFormat() { draft.value = serialize(props.modelValue) }
function editDraft(text: string) { draft.value = text; dirty.value = true; draftError.value = '' }
function applyDraft() {
  try {
    let parsed: unknown
    if (format.value === 'json') parsed = JSON.parse(draft.value)
    else {
      const document = parseDocument(draft.value, { uniqueKeys: true })
      if (document.errors.length || document.warnings.length) throw new Error((document.errors[0] ?? document.warnings[0])!.message)
      parsed = document.toJS({ maxAliasCount: 0 })
    }
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object' || !isJsonValue(parsed)) throw new Error('配置必须是参数名到 JSON 值的对象')
    const values = parsed as ParameterValues
    const error = parameterIssue(props.parameters, values)
    if (error) throw new Error(error)
    dirty.value = false; draftError.value = ''; fieldErrors.value = {}; complexDrafts.value = {}
    emit('update:modelValue', values)
  } catch (error) { draftError.value = error instanceof Error ? error.message : '配置解析失败' }
}
function reset() {
  dirty.value = false; draftError.value = ''; fieldErrors.value = {}; complexDrafts.value = {}
  emit('update:modelValue', mergeParameters(props.parameters, props.defaults))
}
async function importFile(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  if (file.size > 1024 * 1024) { draftError.value = '配置文件不能超过 1 MB'; return }
  format.value = file.name.toLowerCase().endsWith('.json') ? 'json' : 'yaml'
  try { view.value = 'code'; editDraft(await file.text()); applyDraft() }
  catch { draftError.value = '读取配置文件失败，请重新选择文件' }
  ;(event.target as HTMLInputElement).value = ''
}
function exportFile() {
  const blob = new Blob([serialize(props.modelValue)], { type: format.value === 'json' ? 'application/json' : 'application/yaml' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a'); link.href = url; link.download = `parameters.${format.value}`; link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 0)
}
</script>

<template>
  <div class="parameter-editor">
    <template v-if="entries.length">
      <div class="parameter-toolbar" aria-label="参数配置视图">
        <button type="button" class="text-button" :aria-pressed="view === 'common'" @click="switchView('common')">常用参数</button>
        <button type="button" class="text-button" :aria-pressed="view === 'all'" @click="switchView('all')">全部参数（{{ entries.length }}）</button>
        <button type="button" class="text-button" :aria-pressed="view === 'code'" @click="switchView('code')">YAML / JSON</button>
        <button type="button" class="text-button" @click="reset">恢复默认</button>
      </div>
      <div v-if="view !== 'code'" class="parameter-toolbar">
        <input v-model="query" type="search" placeholder="搜索参数名或中文说明" aria-label="搜索参数" />
        <select v-if="groups.length" v-model="group" aria-label="参数分组"><option value="">全部分组</option><option v-for="item in groups" :key="item" :value="item">{{ item }}</option></select>
        <label class="checkbox-label"><input v-model="changedOnly" type="checkbox" />只看修改项</label>
      </div>
      <div v-if="view === 'code'" class="parameter-code">
        <div class="parameter-toolbar">
          <select v-model="format" aria-label="配置格式" :disabled="dirty" @change="changeFormat"><option value="yaml">YAML</option><option value="json">JSON</option></select>
          <label class="text-button">导入配置<input type="file" accept=".yaml,.yml,.json" class="parameter-file" aria-label="导入参数文件" @change="importFile" /></label>
          <button type="button" class="text-button" :disabled="Boolean(issue)" @click="exportFile">导出配置</button>
        </div>
        <textarea :value="draft" rows="14" spellcheck="false" aria-label="专家参数配置" @input="editDraft(($event.target as HTMLTextAreaElement).value)" />
        <button type="button" class="secondary-button" @click="applyDraft">应用配置</button>
        <p v-if="dirty && !draftError" class="muted-copy">编辑后应用配置，再提交任务。</p>
      </div>
      <div v-else class="parameter-fields">
        <label v-for="[name, spec] in filtered" :key="name" class="parameter-control">
          <span class="parameter-title">{{ spec.title ?? name }} <code v-if="spec.title && spec.title !== name">{{ name }}</code></span>
          <small v-if="spec.description">{{ spec.description }}</small>
          <div v-if="controlType(spec) === 'number' || controlType(spec) === 'integer'" class="parameter-inputs">
            <input v-if="bounds(spec).min !== undefined && bounds(spec).max !== undefined" :value="value(name)" type="range" v-bind="bounds(spec)" :aria-label="`${spec.title ?? name}滑块`" @input="update(name, numberInput($event))" />
            <input :value="value(name)" class="parameter-number" type="number" v-bind="bounds(spec)" :aria-label="spec.title ?? name" @input="update(name, numberInput($event))" />
          </div>
          <input v-else-if="controlType(spec) === 'boolean'" :checked="Boolean(value(name))" type="checkbox" role="switch" :aria-label="spec.title ?? name" @change="update(name, ($event.target as HTMLInputElement).checked)" />
          <select v-else-if="controlType(spec) === 'enum'" :value="JSON.stringify(value(name))" :aria-label="spec.title ?? name" @change="update(name, JSON.parse(($event.target as HTMLSelectElement).value))">
            <option v-for="option in (schema(spec).enum as ParameterValue[])" :key="JSON.stringify(option)" :value="JSON.stringify(option)">{{ option === null ? '自动 / null' : String(option) }}</option>
          </select>
          <input v-else-if="controlType(spec) === 'string'" :value="value(name)" :aria-label="spec.title ?? name" @input="update(name, ($event.target as HTMLInputElement).value)" />
          <textarea v-else :value="complexDrafts[name] ?? JSON.stringify(value(name))" rows="2" spellcheck="false" :aria-label="spec.title ?? name" @input="parseComplex(name, ($event.target as HTMLTextAreaElement).value)" />
          <small v-if="fieldErrors[name]" class="parameter-error" role="alert">{{ fieldErrors[name] }}</small>
        </label>
        <p v-if="!filtered.length" class="muted-copy">没有匹配的参数。</p>
      </div>
      <p v-if="draftError" class="parameter-error" role="alert">{{ draftError }}</p>
    </template>
    <p v-else class="muted-copy">此算法无需额外参数。</p>
  </div>
</template>

<style scoped>
.parameter-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: .6rem; margin-bottom: .9rem; }
.parameter-toolbar > input[type='search'] { flex: 1 1 12rem; min-width: 0; }
.parameter-toolbar button[aria-pressed='true'] { font-weight: 700; text-decoration: underline; text-underline-offset: .3rem; }
.parameter-title code { font-size: .8em; font-weight: 400; overflow-wrap: anywhere; }
.parameter-code textarea, .parameter-control textarea { width: 100%; min-width: 0; box-sizing: border-box; font-family: ui-monospace, monospace; }
.parameter-code > button { margin-top: .5rem; }
.parameter-control { min-width: 0; }
.parameter-error { color: var(--danger, #cf3c48); overflow-wrap: anywhere; }
.parameter-file { display: block; max-width: 14rem; font-size: .8rem; }
</style>
