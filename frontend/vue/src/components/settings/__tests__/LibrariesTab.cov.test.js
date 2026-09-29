// src/components/settings/__tests__/LibrariesTab.cov.test.js
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { i18n } from '@/i18n'

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() } }))
const serversFetch = vi.fn().mockResolvedValue(undefined)
const serversList = [{ id: '1', name: 'Emby', type: 'emby' }]
vi.mock('@/stores/servers', () => ({
  useServersStore: () => ({ servers: serversList, fetch: serversFetch }),
}))

import api from '@/api/client'
import LibrariesTab from '../LibrariesTab.vue'

const NAME_PH = () => i18n.global.t('settings.libraries.namePlaceholder')
const ID_PH   = () => i18n.global.t('settings.libraries.libIdPlaceholder')

async function mountTab(libraries = []) {
  api.get.mockResolvedValueOnce({ data: libraries }) // loadLibraries() on mount
  const wrapper = mount(LibrariesTab, { global: { plugins: [i18n], stubs: { teleport: true } } })
  await flushPromises()
  return wrapper
}

async function openCreate(wrapper) {
  api.get.mockResolvedValueOnce({ data: [] }) // loadEmbyLibraries() triggered by openCreate
  const addBtn = wrapper.findAll('button').find(b => b.text().includes(i18n.global.t('settings.libraries.add')))
  await addBtn.trigger('click')
  await flushPromises()
}

const LIB = { id: '9', name: 'Films', server_id: '1', emby_library_id: '42', deletion_unit: 'episode', grace_days: 14, enabled: true, conditions: [{ field: 'x' }] }

describe('LibrariesTab', () => {
  // resetAllMocks (not clearAllMocks): queued mockResolvedValueOnce from a test whose
  // flow diverged (e.g. stopped at validation) must not leak into the next test.
  beforeEach(() => vi.resetAllMocks())

  it('loads and lists libraries with server name, id, unit and grace days', async () => {
    const wrapper = await mountTab([LIB])
    expect(wrapper.text()).toContain('Films')
    expect(wrapper.text()).toContain('Emby')
    expect(wrapper.text()).toContain('42')
  })

  it('shows the empty state when there are no libraries', async () => {
    const wrapper = await mountTab([])
    expect(wrapper.text()).toContain(i18n.global.t('settings.libraries.empty'))
  })

  it('openCreate resets the modal to defaults and preselects the first server', async () => {
    const wrapper = await mountTab([])
    await openCreate(wrapper)
    expect(wrapper.text()).toContain(i18n.global.t('settings.libraries.modalCreate'))
    expect(api.get).toHaveBeenCalledWith('/libraries/emby', { params: { server_id: '1' } })
  })

  it('openEdit pre-fills the modal from the clicked library', async () => {
    const wrapper = await mountTab([LIB])
    api.get.mockResolvedValueOnce({ data: [] }) // loadEmbyLibraries
    const editBtn = wrapper.findAll('button').find(b => b.find('.fa-pen').exists())
    await editBtn.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain(i18n.global.t('settings.libraries.modalEdit'))
    const nameInput = wrapper.find(`input[placeholder="${NAME_PH()}"]`)
    expect(nameInput.element.value).toBe('Films')
  })

  it('save rejects an empty name with a local validation error (no API call)', async () => {
    const wrapper = await mountTab([])
    await openCreate(wrapper)
    const saveBtn = wrapper.findAll('button').find(b => b.text() === i18n.global.t('settings.libraries.modalCreate'))
    await saveBtn.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain(i18n.global.t('settings.libraries.nameRequired'))
    expect(api.post).not.toHaveBeenCalled()
  })

  it('save rejects an empty emby_library_id with a local validation error', async () => {
    const wrapper = await mountTab([])
    await openCreate(wrapper)
    await wrapper.find(`input[placeholder="${NAME_PH()}"]`).setValue('Ma bibliothèque')
    const saveBtn = wrapper.findAll('button').find(b => b.text() === i18n.global.t('settings.libraries.modalCreate'))
    await saveBtn.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain(i18n.global.t('settings.libraries.idRequired'))
    expect(api.post).not.toHaveBeenCalled()
  })

  it('save posts a new library with the trimmed name and closes the modal on success', async () => {
    const wrapper = await mountTab([])
    await openCreate(wrapper)
    await wrapper.find(`input[placeholder="${NAME_PH()}"]`).setValue('  Ma bibliothèque  ')
    await wrapper.find(`input[placeholder="${ID_PH()}"]`).setValue('lib-99')
    api.post.mockResolvedValueOnce({ data: { id: 'new' } })
    api.get.mockResolvedValueOnce({ data: [{ ...LIB, id: 'new', name: 'Ma bibliothèque' }] }) // loadLibraries()
    const saveBtn = wrapper.findAll('button').find(b => b.text() === i18n.global.t('settings.libraries.modalCreate'))
    await saveBtn.trigger('click')
    await flushPromises()
    expect(api.post).toHaveBeenCalledWith('/libraries', expect.objectContaining({ name: 'Ma bibliothèque', emby_library_id: 'lib-99' }))
    expect(serversFetch).toHaveBeenCalled()
    expect(wrapper.text()).not.toContain(i18n.global.t('settings.libraries.modalCreate'))
  })

  it('save shows the server error detail and keeps the modal open on failure', async () => {
    const wrapper = await mountTab([])
    await openCreate(wrapper)
    await wrapper.find(`input[placeholder="${NAME_PH()}"]`).setValue('X')
    await wrapper.find(`input[placeholder="${ID_PH()}"]`).setValue('id1')
    api.post.mockRejectedValueOnce({ response: { data: { detail: 'ID déjà utilisé' } } })
    const saveBtn = wrapper.findAll('button').find(b => b.text() === i18n.global.t('settings.libraries.modalCreate'))
    await saveBtn.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('ID déjà utilisé')
    expect(wrapper.text()).toContain(i18n.global.t('settings.libraries.modalCreate')) // modal still open
  })

  it('askDelete + confirm deletes the library via DELETE /libraries/:id', async () => {
    const wrapper = await mountTab([LIB])
    const delBtn = wrapper.findAll('button').find(b => b.find('.fa-trash:not(.fa-trash-can)').exists())
    await delBtn.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain(i18n.global.t('settings.libraries.deleteConfirm', { name: 'Films' }))
    api.delete.mockResolvedValueOnce({ data: {} })
    api.get.mockResolvedValueOnce({ data: [] }) // loadLibraries() after delete
    const confirmBtn = wrapper.findAll('button').find(b => b.text() === i18n.global.t('common.delete') && !b.attributes('title'))
    await confirmBtn.trigger('click')
    await flushPromises()
    expect(api.delete).toHaveBeenCalledWith('/libraries/9')
  })

  it('cancelling the delete confirmation does not call the API', async () => {
    const wrapper = await mountTab([LIB])
    const delBtn = wrapper.findAll('button').find(b => b.find('.fa-trash:not(.fa-trash-can)').exists())
    await delBtn.trigger('click')
    await flushPromises()
    const cancelBtn = wrapper.findAll('button').find(b => b.text() === i18n.global.t('common.cancel'))
    await cancelBtn.trigger('click')
    expect(api.delete).not.toHaveBeenCalled()
  })

  it('triggerScan posts /libraries/:id/scan and shows a temporary success message', async () => {
    vi.useFakeTimers()
    const wrapper = await mountTab([LIB])
    api.post.mockResolvedValueOnce({ data: {} })
    const scanBtn = wrapper.findAll('button').find(b => b.find('.fa-play').exists())
    await scanBtn.trigger('click')
    await flushPromises()
    expect(api.post).toHaveBeenCalledWith('/libraries/9/scan')
    expect(wrapper.text()).toContain(i18n.global.t('settings.libraries.scanLaunched', { name: 'Films' }))
    await vi.advanceTimersByTimeAsync(4000)
    expect(wrapper.text()).not.toContain(i18n.global.t('settings.libraries.scanLaunched', { name: 'Films' }))
    vi.useRealTimers()
  })

  it('a library with no conditions shows the "no conditions" hint instead of a count', async () => {
    const wrapper = await mountTab([{ ...LIB, conditions: [] }])
    expect(wrapper.text()).toContain(i18n.global.t('settings.libraries.noConditions'))
  })
})
