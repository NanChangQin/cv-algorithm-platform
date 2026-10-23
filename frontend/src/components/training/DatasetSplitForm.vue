<script setup lang="ts">
import { computed } from 'vue'
import type { DatasetSplitConfig } from '@/api/training'

const props = defineProps<{ modelValue: DatasetSplitConfig; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: DatasetSplitConfig] }>()
function update(key: keyof DatasetSplitConfig, value: string) {
  const numeric = ['train', 'val', 'test', 'seed'].includes(key)
  const number = value === '' ? NaN : Number(value)
  emit('update:modelValue', { ...props.modelValue, [key]: numeric ? (key === 'seed' ? number : number / 100) : value })
}
const issue = computed(() => {
  const c = props.modelValue
  if (![c.train, c.val, c.test].every(Number.isFinite) || c.train <= 0 || c.val <= 0 || c.test < 0 || Math.abs(c.train + c.val + c.test - 1) > 1e-8) return '训练、验证比例须大于 0，测试比例不小于 0，合计 100%'
  if (!Number.isInteger(c.seed) || c.seed < 0 || c.seed > 4294967295) return '随机种子须为 0～4294967295 的整数'
  return ''
})
defineExpose({ issue })
</script>

<template>
  <fieldset class="dataset-split-form" :disabled="disabled">
    <legend>划分与校验</legend>
    <div class="training-form-grid">
      <label class="field-label">划分方式
        <select :value="modelValue.mode" @change="update('mode', ($event.target as HTMLSelectElement).value)">
          <option value="preserve">保留已有 train / val / test</option>
          <option value="reshuffle">按比例重新划分全部图片</option>
        </select>
      </label>
      <label class="field-label">标注校验
        <select :value="modelValue.validation" @change="update('validation', ($event.target as HTMLSelectElement).value)">
          <option value="repair">审计修复：删除错误行并保留报告</option>
          <option value="strict">严格：错误标注或孤立标注拒绝导入</option>
        </select>
      </label>
      <label v-for="key in (['train', 'val', 'test'] as const)" :key="key" class="field-label">
        {{ { train: '训练', val: '验证', test: '测试' }[key] }}比例（%）
        <input :value="Number((modelValue[key] * 100).toFixed(8))" type="number" min="0" max="100" step="1" :aria-label="`${key} 比例`" @input="update(key, ($event.target as HTMLInputElement).value)" />
      </label>
      <label class="field-label">随机种子
        <input :value="modelValue.seed" type="number" min="0" max="4294967295" step="1" aria-label="划分随机种子" @input="update('seed', ($event.target as HTMLInputElement).value)" />
      </label>
    </div>
    <p v-if="modelValue.mode === 'preserve'" class="muted-copy">已有划分原样保留；缺少 val 时，仅从 train 按训练∶验证比例抽取，test 始终独立。无划分的图片归入 train。</p>
    <p v-else class="build-notice">将重新分配全部图片，包括原 test；若要保留独立测试集，请选“保留已有划分”。重复内容始终分在同一集合，比例可能因分组和取整略有差异。</p>
    <p v-if="issue" class="alert" role="alert">{{ issue }}</p>
  </fieldset>
</template>

<style scoped>
.dataset-split-form { border: 1px solid var(--border-color, #ddd); border-radius: 8px; padding: 16px; margin: 12px 0; }
legend { padding: 0 8px; font-weight: 600; }
</style>
