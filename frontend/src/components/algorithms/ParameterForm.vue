<script setup lang="ts">
import type { ParameterSpec } from '@/types/algorithm'
import { numericBounds, type ParameterValues } from '@/utils/parameters'

const props = defineProps<{ parameters: Record<string, ParameterSpec>; modelValue: ParameterValues }>()
const emit = defineEmits<{ 'update:modelValue': [value: ParameterValues] }>()

function update(name: string, value: string | number | boolean) {
  emit('update:modelValue', { ...props.modelValue, [name]: value })
}
function numberInput(event: Event) {
  const raw = (event.target as HTMLInputElement).value
  return raw === '' ? Number.NaN : Number(raw)
}
</script>

<template>
  <div class="parameter-fields">
    <template v-for="(spec, name) in parameters" :key="name">
      <label v-if="spec.type === 'number' || spec.type === 'integer'" class="parameter-control">
        <span class="parameter-title">{{ spec.title ?? name }}</span>
        <small v-if="spec.description">{{ spec.description }}</small>
        <div class="parameter-inputs">
          <input :value="modelValue[name]" type="range" v-bind="numericBounds(spec)" :aria-label="`${spec.title ?? name}滑块`" @input="update(String(name), numberInput($event))" />
          <input :value="modelValue[name]" class="parameter-number" type="number" v-bind="numericBounds(spec)" :aria-label="spec.title ?? String(name)" @input="update(String(name), numberInput($event))" />
        </div>
      </label>
      <label v-else-if="spec.type === 'boolean'" class="check-control">
        <span>{{ spec.title ?? name }}</span>
        <input :checked="Boolean(modelValue[name])" type="checkbox" role="switch" @change="update(String(name), ($event.target as HTMLInputElement).checked)" />
      </label>
      <label v-else class="parameter-control">
        <span class="parameter-title">{{ spec.title ?? name }}</span>
        <small v-if="spec.description">{{ spec.description }}</small>
        <select v-if="spec.options" :value="modelValue[name]" @change="update(String(name), ($event.target as HTMLSelectElement).value)">
          <option v-for="option in spec.options" :key="option" :value="option">{{ option }}</option>
        </select>
        <input v-else :value="modelValue[name]" @input="update(String(name), ($event.target as HTMLInputElement).value)" />
      </label>
    </template>
    <p v-if="!Object.keys(parameters).length" class="muted-copy">此算法无需额外参数。</p>
  </div>
</template>
