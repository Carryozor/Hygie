// src/components/settings/__tests__/QbitTab.cov.test.js
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { i18n } from '@/i18n'

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), post: vi.fn() } }))
import api from '@/api/client'
import QbitTab from '../QbitTab.vue'

function mountTab(formOverrides = {}) {
  return mount(QbitTab, {
    props: {
      form: {
        qbit_url: '', qbit_user: '', qbit_password: '', qbit_proxy_url: '',
        qbit_action: '', qbit_tag: '', ...formOverrides,
      },
    },
    global: { plugins: [i18n] },
  })
}

describe('QbitTab', () => {
  beforeEach(() => vi.clearAllMocks())

  it('reveals the masked password via GET /settings/reveal/qbit_password', async () => {
    api.get.mockResolvedValueOnce({ data: { value: 'realpwd' } })
    const wrapper = mountTab({ qbit_password: '***' })
    const eyeBtns = wrapper.findAll('button').filter(b => b.find('.fa-eye').exists())
    await eyeBtns[0].trigger('click')
    await flushPromises()
    expect(api.get).toHaveBeenCalledWith('/settings/reveal/qbit_password')
    const revealed = wrapper.findAll('input').find(i => i.element.value === 'realpwd')
    expect(revealed.attributes('type')).toBe('text')
  })

  it('does not reveal an already-real password value', async () => {
    const wrapper = mountTab({ qbit_password: 'real-already' })
    const eyeBtns = wrapper.findAll('button').filter(b => b.find('.fa-eye').exists())
    await eyeBtns[0].trigger('click')
    await flushPromises()
    expect(api.get).not.toHaveBeenCalled()
  })

  it('the QUI proxy TestBtn only appears once a proxy URL is set', async () => {
    const wrapperNoProxy = mountTab({ qbit_proxy_url: '' })
    expect(wrapperNoProxy.findAll('button').filter(b => b.text() === 'Tester')).toHaveLength(1) // qbit only
    const wrapperWithProxy = mountTab({ qbit_proxy_url: 'http://qui:7476' })
    expect(wrapperWithProxy.findAll('button').filter(b => b.text() === 'Tester')).toHaveLength(2) // qbit + qui
  })

  it('reveals the masked qbit_proxy_url via GET /settings/reveal/qbit_proxy_url', async () => {
    api.get.mockResolvedValueOnce({ data: { value: 'http://real-qui:7476' } })
    const wrapper = mountTab({ qbit_proxy_url: '***' })
    const eyeBtns = wrapper.findAll('button').filter(b => b.find('.fa-eye').exists())
    await eyeBtns[1].trigger('click')
    await flushPromises()
    expect(api.get).toHaveBeenCalledWith('/settings/reveal/qbit_proxy_url')
    const revealed = wrapper.findAll('input').find(i => i.element.value === 'http://real-qui:7476')
    expect(revealed).toBeTruthy()
  })

  it('toggling the "pause" action slider sets qbit_action to "pause"', async () => {
    const form = { qbit_url: '', qbit_user: '', qbit_password: '', qbit_proxy_url: '', qbit_action: '', qbit_tag: '' }
    const wrapper = mount(QbitTab, { props: { form }, global: { plugins: [i18n] } })
    const toggles = wrapper.findAll('input[type=checkbox]')
    await toggles[0].setValue(true)
    expect(form.qbit_action).toBe('pause')
  })

  it('toggling the same action slider off clears qbit_action back to empty', async () => {
    const form = { qbit_url: '', qbit_user: '', qbit_password: '', qbit_proxy_url: '', qbit_action: 'pause', qbit_tag: '' }
    const wrapper = mount(QbitTab, { props: { form }, global: { plugins: [i18n] } })
    const toggles = wrapper.findAll('input[type=checkbox]')
    expect(toggles[0].element.checked).toBe(true)
    await toggles[0].setValue(false)
    expect(form.qbit_action).toBe('')
  })

  it('toggling "delete with files" sets qbit_action to "delete_files"', async () => {
    const form = { qbit_url: '', qbit_user: '', qbit_password: '', qbit_proxy_url: '', qbit_action: '', qbit_tag: '' }
    const wrapper = mount(QbitTab, { props: { form }, global: { plugins: [i18n] } })
    const toggles = wrapper.findAll('input[type=checkbox]')
    await toggles[2].setValue(true)
    expect(form.qbit_action).toBe('delete_files')
  })

  it('enabling the tag toggle fills qbit_tag with the default tag name when empty', async () => {
    const form = { qbit_url: '', qbit_user: '', qbit_password: '', qbit_proxy_url: '', qbit_action: '', qbit_tag: '' }
    const wrapper = mount(QbitTab, { props: { form }, global: { plugins: [i18n] } })
    const toggles = wrapper.findAll('input[type=checkbox]')
    await toggles[3].setValue(true)
    await flushPromises()
    expect(form.qbit_tag).toBe(i18n.global.t('settings.qbit.tagDefault'))
  })

  it('enabling the tag toggle preserves an existing custom tag name', async () => {
    const form = { qbit_url: '', qbit_user: '', qbit_password: '', qbit_proxy_url: '', qbit_action: '', qbit_tag: '' }
    // tagEnabled is computed from !!form.qbit_tag, so start with a tag already set (toggle already "on")
    form.qbit_tag = 'hygie-custom'
    const wrapper = mount(QbitTab, { props: { form }, global: { plugins: [i18n] } })
    const tagInput = wrapper.find(`input[placeholder="${i18n.global.t('settings.qbit.tagDefault')}"]`)
    expect(tagInput.element.value).toBe('hygie-custom')
  })

  it('disabling the tag toggle clears qbit_tag to empty', async () => {
    const form = { qbit_url: '', qbit_user: '', qbit_password: '', qbit_proxy_url: '', qbit_action: '', qbit_tag: 'hygie-custom' }
    const wrapper = mount(QbitTab, { props: { form }, global: { plugins: [i18n] } })
    const tagToggle = wrapper.findAll('input[type=checkbox]')[3]
    await tagToggle.setValue(false)
    expect(form.qbit_tag).toBe('')
  })

  it('editing the URL/user fields mutates the shared form prop', async () => {
    const form = { qbit_url: '', qbit_user: '', qbit_password: '', qbit_proxy_url: '', qbit_action: '', qbit_tag: '' }
    const wrapper = mount(QbitTab, { props: { form }, global: { plugins: [i18n] } })
    await wrapper.find('input[type=url]').setValue('http://qbt:8080')
    await wrapper.find('input[type=text]').setValue('admin')
    expect(form.qbit_url).toBe('http://qbt:8080')
    expect(form.qbit_user).toBe('admin')
  })
})
