import Ajv2020 from 'ajv/dist/2020'
import type { ValidateFunction } from 'ajv'
import type { JsonSchema, JsonValue, ParameterSpec } from '@/types/algorithm'

export type ParameterValue = JsonValue
export type ParameterValues = Record<string, ParameterValue>
const ajv = new Ajv2020({ strict: false, allErrors: false, validateFormats: false })
const validators = new Map<string, ValidateFunction>()

export function schemaForParameter(spec: ParameterSpec): JsonSchema {
  if (spec.type === 'json') return spec.json_schema
  if (spec.type === 'string') return { type: spec.type, ...(spec.options ? { enum: spec.options } : {}) }
  if (spec.type === 'boolean') return { type: spec.type }
  return { type: spec.type, ...(spec.minimum == null ? {} : { minimum: spec.minimum }), ...(spec.maximum == null ? {} : { maximum: spec.maximum }) }
}

export function defaultParameters(parameters: Record<string, ParameterSpec> = {}): ParameterValues {
  return JSON.parse(JSON.stringify(Object.fromEntries(Object.entries(parameters).map(([name, spec]) => [name, spec.default]))))
}

/** Retain declared JSON values without flattening arrays, nulls or nested objects. */
export function mergeParameters(parameters: Record<string, ParameterSpec> = {}, values: Record<string, unknown> = {}): ParameterValues {
  const merged = defaultParameters(parameters)
  for (const name of Object.keys(parameters)) {
    if (Object.prototype.hasOwnProperty.call(values, name) && isJsonValue(values[name])) merged[name] = JSON.parse(JSON.stringify(values[name]))
  }
  return merged
}

export function isJsonValue(value: unknown): value is JsonValue {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return true
  if (typeof value === 'number') return Number.isFinite(value)
  if (Array.isArray(value)) return value.every(isJsonValue)
  return typeof value === 'object' && value !== null && Object.values(value).every(isJsonValue)
}

export function parameterIssue(parameters: Record<string, ParameterSpec> = {}, values: ParameterValues): string {
  const unknown = Object.keys(values).filter((name) => !Object.prototype.hasOwnProperty.call(parameters, name))
  if (unknown.length) return `未知参数：${unknown.join(', ')}`
  for (const [name, spec] of Object.entries(parameters)) {
    const value = Object.prototype.hasOwnProperty.call(values, name) ? values[name] : spec.default
    const title = spec.title ?? name
    if (!isJsonValue(value)) return `请填写有效的${title}`
    if (spec.status && spec.status !== 'configurable') return `${title}：${spec.reason}`
    try {
      const schema = schemaForParameter(spec)
      const key = JSON.stringify(schema)
      let validate = validators.get(key)
      if (!validate) { validate = ajv.compile(schema); validators.set(key, validate) }
      if (!validate(value)) {
        const error = validate.errors?.[0]
        if (error?.keyword === 'maximum') return `${title}不能大于 ${schema.maximum}`
        if (error?.keyword === 'minimum') return `${title}不能小于 ${schema.minimum}`
        if (error?.keyword === 'multipleOf') return `${title}必须是 ${schema.multipleOf} 的倍数`
        return `${title}：${error?.message ?? '参数格式不正确'}`
      }
    } catch { return `${title}的参数契约无效` }
  }
  return ''
}

export function numericBounds(spec: ParameterSpec) {
  const schema = schemaForParameter(spec)
  return { min: schema.minimum as number | undefined, max: schema.maximum as number | undefined, step: (schema.multipleOf ?? ('step' in spec ? spec.step : undefined) ?? (schema.type === 'integer' ? 1 : 0.01)) as number }
}

export function parameterRange(spec: ParameterSpec): string {
  const schema = schemaForParameter(spec)
  const parts: string[] = []
  if (schema.minimum !== undefined || schema.maximum !== undefined) parts.push(`${schema.minimum ?? '−∞'} – ${schema.maximum ?? '+∞'}`)
  if (Array.isArray(schema.enum)) parts.push(schema.enum.map((value) => JSON.stringify(value)).join(' / '))
  parts.push(`默认 ${JSON.stringify(spec.default)}`)
  return parts.join('，')
}

export const parameterTypeLabel: Record<ParameterSpec['type'], string> = {
  number: '小数', integer: '整数', boolean: '开关', string: '文本', json: '结构化配置',
}
