import type { ParameterSpec } from '@/types/algorithm'

export type ParameterValue = string | number | boolean
export type ParameterValues = Record<string, ParameterValue>

export function defaultParameters(parameters: Record<string, ParameterSpec> = {}): ParameterValues {
  return Object.fromEntries(Object.entries(parameters).map(([name, spec]) => [name, spec.default]))
}

/** 只保留算法声明过的参数，并补齐缺省值（复用旧任务配置时，算法参数可能已经变化）。 */
export function mergeParameters(parameters: Record<string, ParameterSpec> = {}, values: Record<string, unknown> = {}): ParameterValues {
  const merged = defaultParameters(parameters)
  for (const name of Object.keys(parameters)) {
    const value = values[name]
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') merged[name] = value
  }
  return merged
}

export function parameterIssue(parameters: Record<string, ParameterSpec> = {}, values: ParameterValues): string {
  for (const [name, spec] of Object.entries(parameters)) {
    if (spec.type !== 'number' && spec.type !== 'integer') continue
    const value = values[name]
    const title = spec.title ?? name
    if (typeof value !== 'number' || !Number.isFinite(value)) return `请填写${title}`
    if (spec.type === 'integer' && !Number.isInteger(value)) return `${title}需要填写整数`
    if (spec.minimum !== undefined && value < spec.minimum) return `${title}不能小于 ${spec.minimum}`
    if (spec.maximum !== undefined && value > spec.maximum) return `${title}不能大于 ${spec.maximum}`
  }
  return ''
}

export function numericBounds(spec: ParameterSpec) {
  return spec.type === 'number' || spec.type === 'integer'
    ? { min: spec.minimum, max: spec.maximum, step: spec.step ?? (spec.type === 'integer' ? 1 : 0.01) }
    : {}
}

/** 参数说明里的取值范围文字，例如“0 – 1，默认 0.5” */
export function parameterRange(spec: ParameterSpec): string {
  const parts: string[] = []
  if (spec.type === 'number' || spec.type === 'integer') {
    if (spec.minimum !== undefined || spec.maximum !== undefined) parts.push(`${spec.minimum ?? '−∞'} – ${spec.maximum ?? '+∞'}`)
  } else if (spec.type === 'string' && spec.options?.length) {
    parts.push(spec.options.join(' / '))
  }
  parts.push(`默认 ${spec.type === 'boolean' ? (spec.default ? '开启' : '关闭') : spec.default}`)
  return parts.join('，')
}

export const parameterTypeLabel: Record<ParameterSpec['type'], string> = {
  number: '小数', integer: '整数', boolean: '开关', string: '文本',
}
