import { flushPromises, mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { describe, expect, it } from 'vitest'
import ParameterForm from '../ParameterForm.vue'
import type { ParameterSpec } from '@/types/algorithm'
import { defaultParameters, mergeParameters, parameterIssue } from '@/utils/parameters'

const specs: Record<string, ParameterSpec> = {
  count: { type: 'integer', default: 10, minimum: 1, title: '数量', common: true },
  classes: { type: 'json', default: null, title: '类别', group: '筛选', json_schema: {
    anyOf: [{ type: 'null' }, { type: 'array', items: { type: 'integer', minimum: 0 } }],
  } },
  nested: { type: 'json', default: { flags: [false, null, 0] }, json_schema: { type: 'object' } },
}

describe('parameter contracts and editor', () => {
  it('clones reactive complex defaults and keeps null/false/zero values', () => {
    const defaults = defaultParameters(reactive(specs))
    expect(defaults.nested).toEqual({ flags: [false, null, 0] })
    expect(mergeParameters(specs, { classes: [0, 2] }).classes).toEqual([0, 2])
    expect(parameterIssue(specs, { count: true })).toContain('数量')
    expect(parameterIssue(specs, { classes: [true] })).toContain('类别')
    expect(parameterIssue(specs, { count: Infinity })).not.toBe('')
    expect(parameterIssue(specs, { typo: 1 })).toContain('未知参数')
  })

  it('searches beyond common fields and preserves typed JSON values', async () => {
    const wrapper = mount(ParameterForm, { props: { parameters: specs, modelValue: {} } })
    expect(wrapper.find('textarea').exists()).toBe(false)
    await wrapper.find('input[type="search"]').setValue('类别')
    await wrapper.find('textarea').setValue('[0, 2]')
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual([{ classes: [0, 2] }])
    await wrapper.find('textarea').setValue('[false]')
    expect(wrapper.emitted('validity')!.at(-1)![0]).not.toBe('')
  })

  it('blocks dirty drafts and validates YAML keys before applying', async () => {
    const wrapper = mount(ParameterForm, { props: { parameters: specs, modelValue: { count: 10 } } })
    await wrapper.findAll('button').find((item) => item.text() === 'YAML / JSON')!.trigger('click')
    const draft = wrapper.find('textarea[aria-label="专家参数配置"]')
    await draft.setValue('count: 3\nclasses: [0, 2]\nnested: {flags: [false, null, 0]}')
    expect(wrapper.emitted('validity')!.at(-1)).toEqual(['请先应用专家配置'])
    await wrapper.findAll('button').find((item) => item.text() === '应用配置')!.trigger('click')
    const values = { count: 3, classes: [0, 2], nested: { flags: [false, null, 0] } }
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual([values])
    await draft.setValue('count: 1\ncount: 2')
    await wrapper.findAll('button').find((item) => item.text() === '应用配置')!.trigger('click')
    expect(wrapper.find('[role="alert"]').text()).toContain('unique')
    await draft.setValue('typo: 1')
    await wrapper.findAll('button').find((item) => item.text() === '应用配置')!.trigger('click')
    expect(wrapper.text()).toContain('未知参数')
    await draft.setValue('nested: &x {flags: [*x]}')
    await wrapper.findAll('button').find((item) => item.text() === '应用配置')!.trigger('click')
    expect(wrapper.emitted('validity')!.at(-1)![0]).not.toBe('')
  })

  it('applies JSON without flattening objects and rejects nonfinite YAML', async () => {
    const wrapper = mount(ParameterForm, { props: { parameters: specs, modelValue: {} } })
    await wrapper.findAll('button').find((item) => item.text() === 'YAML / JSON')!.trigger('click')
    await wrapper.find('select[aria-label="配置格式"]').setValue('json')
    await wrapper.find('textarea').setValue('{"classes":null,"nested":{"zero":0,"bool":false}}')
    await wrapper.findAll('button').find((item) => item.text() === '应用配置')!.trigger('click')
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual([{ classes: null, nested: { zero: 0, bool: false } }])
    await wrapper.find('select[aria-label="配置格式"]').setValue('yaml')
    await wrapper.find('textarea').setValue('count: .inf')
    await wrapper.findAll('button').find((item) => item.text() === '应用配置')!.trigger('click')
    await flushPromises()
    expect(wrapper.emitted('validity')!.at(-1)![0]).not.toBe('')
  })
})
