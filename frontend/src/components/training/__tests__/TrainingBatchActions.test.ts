import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import TrainingBatchActions from '../TrainingBatchActions.vue'
const api = vi.hoisted(() => ({preview:vi.fn(),create:vi.fn(),get:vi.fn()}))
vi.mock('@/api/trainingBatches', () => ({trainingBatchesApi:{...api,download:(id:string)=>`/batches/${id}/download`}}))
const result = {id:'job',request:{action:'delete',content:'all',run_ids:['a','b']},status:'completed',processed:2,total:2,current:'',error:'',download_ready:false,items:[{id:'a',name:'A',status:'success',reason:''},{id:'b',name:'B',status:'failed',reason:'节点暂时离线'}]}
beforeEach(()=>{
  vi.clearAllMocks(); localStorage.clear()
  HTMLDialogElement.prototype.showModal = function(){this.setAttribute('open','')}
  HTMLDialogElement.prototype.close = function(){this.removeAttribute('open')}
  api.preview.mockResolvedValue({items:[{id:'a',name:'A',eligible:true,reason:''},{id:'b',name:'B',eligible:false,reason:'正在运行'}]})
  api.create.mockResolvedValue(result); api.get.mockResolvedValue(result)
})
describe('batch confirmation and feedback',()=>{
  it('submits a UUID on LAN HTTP without randomUUID',async()=>{
    const getRandomValues = crypto.getRandomValues.bind(crypto)
    vi.stubGlobal('crypto', {getRandomValues})
    const wrapper = mount(TrainingBatchActions)
    try {
      await wrapper.vm.open(['a'], 'delete')
      await wrapper.get('dialog .danger-button').trigger('click')
      await flushPromises()
      expect(api.create).toHaveBeenCalledOnce()
      expect(api.create.mock.calls[0]![0].request_id).toMatch(
        /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
      )
    } finally {
      wrapper.unmount()
      vi.unstubAllGlobals()
    }
  })
  it('unlocks the dialog if preparing the submission fails',async()=>{
    const wrapper = mount(TrainingBatchActions)
    const write = vi.spyOn(Storage.prototype, 'setItem')
    try {
      await wrapper.vm.open(['a'], 'delete')
      write.mockImplementationOnce(() => { throw new Error('存储不可用') })
      await wrapper.get('dialog .danger-button').trigger('click')
      await flushPromises()
      expect(api.create).not.toHaveBeenCalled()
      expect(wrapper.get('dialog [role="alert"]').text()).toContain('存储不可用')
      expect(wrapper.get('dialog .secondary-button').attributes('disabled')).toBeUndefined()
      await wrapper.get('dialog .danger-button').trigger('click')
      await flushPromises()
      expect(api.create).toHaveBeenCalledOnce()
    } finally {
      write.mockRestore()
      wrapper.unmount()
    }
  })
  it('does not delete until confirmed and retries only failed items',async()=>{
    const wrapper=mount(TrainingBatchActions)
    await wrapper.vm.open(['a','b'],'delete');await flushPromises()
    expect(api.create).not.toHaveBeenCalled()
    expect(wrapper.find('dialog').text()).toContain('可删除 1 项，跳过 1 项')
    await wrapper.find('dialog .danger-button').trigger('click');await flushPromises()
    expect(api.create).toHaveBeenCalledWith(expect.objectContaining({run_ids:['a','b'],action:'delete'}))
    expect(wrapper.emitted('removed')?.[0]).toEqual([['a']])
    expect(wrapper.find('[aria-label="批量操作进度"]').text()).toContain('节点暂时离线')
    await wrapper.findAll('button').find(b=>b.text()==='重试未完成的 1 项')!.trigger('click');await flushPromises()
    expect(api.preview).toHaveBeenLastCalledWith(['b'])
    wrapper.unmount()
  })
  it('keeps a stable idempotency key when submission loses its response',async()=>{
    api.create.mockRejectedValueOnce(new Error('连接中断')).mockResolvedValueOnce(result)
    const wrapper=mount(TrainingBatchActions)
    await wrapper.vm.open(['a','b'],'delete');await flushPromises()
    await wrapper.find('dialog .danger-button').trigger('click');await flushPromises()
    const key=api.create.mock.calls[0]![0].request_id
    expect(wrapper.find('dialog').attributes('open')).toBeDefined()
    await wrapper.find('dialog .danger-button').trigger('click');await flushPromises()
    expect(api.create.mock.calls[1]![0].request_id).toBe(key)
    wrapper.unmount()
  })
  it('restores progress after refresh and offers a single ZIP',async()=>{
    localStorage.setItem('cv-training-batch:default','job')
    api.get.mockResolvedValue({...result,request:{...result.request,action:'download'},download_ready:true})
    const wrapper=mount(TrainingBatchActions);await flushPromises()
    expect(api.get).toHaveBeenCalledWith('job')
    expect(wrapper.find('a[download]').attributes('href')).toBe('/batches/job/download')
    expect(api.create).not.toHaveBeenCalled()
    wrapper.unmount()
  })
  it('repackages all items when a download was interrupted before its ZIP was ready',async()=>{
    localStorage.setItem('cv-training-batch:default','job')
    api.get.mockResolvedValue({...result,request:{...result.request,action:'download'},status:'failed',items:result.items.map(item=>({...item,status:'success'}))})
    const wrapper=mount(TrainingBatchActions);await flushPromises()
    await wrapper.findAll('button').find(b=>b.text()==='重试未完成的 2 项')!.trigger('click');await flushPromises()
    await wrapper.find('dialog .primary-button').trigger('click');await flushPromises()
    expect(api.create).toHaveBeenCalledWith(expect.objectContaining({action:'download',run_ids:['a','b']}))
    wrapper.unmount()
  })
})
