// src/components/settings/__tests__/GeneralTab.cov.test.js
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { i18n } from '@/i18n'

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }))
import api from '@/api/client'
import GeneralTab from '../GeneralTab.vue'

function baseForm(overrides = {}) {
  return {
    log_level: 'INFO', max_parallel_library_scans: 2,
    scan_interval_minutes: 60, deletion_check_interval_minutes: 60,
    deleted_retention_days: 30, log_retention_days: 30, job_history_retention_days: 30,
    backup_enabled: true, backup_interval_hours: 24, backup_retention_count: 5, backup_path: '/app/data/backups',
    public_dashboard_enabled: false, public_dashboard_slug: '', public_dashboard_password: '',
    ...overrides,
  }
}

function mountTab(formOverrides = {}) {
  return mount(GeneralTab, { props: { form: baseForm(formOverrides) }, global: { plugins: [i18n] } })
}

describe('GeneralTab', () => {
  beforeEach(() => vi.clearAllMocks())

  it('loads the existing backup list on mount', async () => {
    api.get.mockResolvedValueOnce({ data: [{ filename: 'backup-1.sql', size_mb: 4.2 }] })
    const wrapper = mountTab()
    await flushPromises()
    expect(wrapper.text()).toContain('backup-1.sql')
    expect(wrapper.text()).toContain('4.2 MB')
  })

  it('triggerBackup posts /backup, shows a success message with the filename, and reloads the list', async () => {
    api.get.mockResolvedValueOnce({ data: [] })
    const wrapper = mountTab()
    await flushPromises()
    api.post.mockResolvedValueOnce({ data: { filename: 'backup-new.sql' } })
    api.get.mockResolvedValueOnce({ data: [{ filename: 'backup-new.sql', size_mb: 1 }] })
    const backupBtn = wrapper.findAll('button').find(b => b.text().includes(i18n.global.t('settings.general.backup.manual')))
    await backupBtn.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('backup-new.sql')
    expect(wrapper.find('.text-green-400').exists()).toBe(true)
  })

  it('triggerBackup shows a failure message and does not crash when the API rejects', async () => {
    api.get.mockResolvedValueOnce({ data: [] })
    const wrapper = mountTab()
    await flushPromises()
    api.post.mockRejectedValueOnce(new Error('disk full'))
    const backupBtn = wrapper.findAll('button').find(b => b.text().includes(i18n.global.t('settings.general.backup.manual')))
    await backupBtn.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain(i18n.global.t('settings.general.backup.failed'))
  })

  it('deleteBackup removes the entry from the visible list on success', async () => {
    api.get.mockResolvedValueOnce({ data: [{ filename: 'old.sql', size_mb: 2 }] })
    const wrapper = mountTab()
    await flushPromises()
    api.delete.mockResolvedValueOnce({ data: {} })
    const delBtn = wrapper.find('button[title]') // delete button has a title attr
    await delBtn.trigger('click')
    await flushPromises()
    expect(api.delete).toHaveBeenCalledWith('/backup/old.sql')
    expect(wrapper.text()).not.toContain('old.sql')
  })

  it('deleteBackup URL-encodes filenames containing special characters', async () => {
    api.get.mockResolvedValueOnce({ data: [{ filename: 'backup 2026-01-01.sql', size_mb: 2 }] })
    const wrapper = mountTab()
    await flushPromises()
    api.delete.mockResolvedValueOnce({ data: {} })
    const delBtn = wrapper.find('button[title]')
    await delBtn.trigger('click')
    await flushPromises()
    expect(api.delete).toHaveBeenCalledWith(`/backup/${encodeURIComponent('backup 2026-01-01.sql')}`)
  })

  it('leaves the backup list unchanged when deletion fails', async () => {
    api.get.mockResolvedValueOnce({ data: [{ filename: 'old.sql', size_mb: 2 }] })
    const wrapper = mountTab()
    await flushPromises()
    api.delete.mockRejectedValueOnce(new Error('locked'))
    const delBtn = wrapper.find('button[title]')
    await delBtn.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('old.sql')
  })

  it('computes publicUrl as just the origin (no trailing slug) when no slug is set', async () => {
    api.get.mockResolvedValueOnce({ data: [] })
    const wrapper = mountTab({ public_dashboard_enabled: true, public_dashboard_slug: '' })
    await flushPromises()
    expect(wrapper.find('code').text()).toBe(window.location.origin)
  })

  it('computes publicUrl with the trimmed slug appended when set', async () => {
    api.get.mockResolvedValueOnce({ data: [] })
    const wrapper = mountTab({ public_dashboard_enabled: true, public_dashboard_slug: '  my-family  ' })
    await flushPromises()
    expect(wrapper.find('code').text()).toBe(`${window.location.origin}/my-family`)
  })

  it('hides the slug/password fields entirely when public_dashboard_enabled is false', async () => {
    api.get.mockResolvedValueOnce({ data: [] })
    const wrapper = mountTab({ public_dashboard_enabled: false })
    await flushPromises()
    expect(wrapper.text()).not.toContain(i18n.global.t('settings.general.publicDashboard.slug', "Segment d'URL (optionnel)"))
  })

  it('editing the log level select, scan interval, and backup path mutate the shared form prop in place', async () => {
    api.get.mockResolvedValueOnce({ data: [] })
    const form = baseForm()
    const wrapper = mount(GeneralTab, { props: { form }, global: { plugins: [i18n] } })
    await flushPromises()
    await wrapper.find('select').setValue('DEBUG')
    expect(form.log_level).toBe('DEBUG')
    await wrapper.findAll('input[type=number]')[0].setValue('5')
    expect(form.max_parallel_library_scans).toBe(5)
    const pathInput = wrapper.find('input[placeholder="/app/data/backups"]')
    await pathInput.setValue('/mnt/backups')
    expect(form.backup_path).toBe('/mnt/backups')
  })

  it('toggling the backup_enabled slider flips the shared form.backup_enabled prop', async () => {
    api.get.mockResolvedValueOnce({ data: [] })
    const form = baseForm({ backup_enabled: false })
    const wrapper = mount(GeneralTab, { props: { form }, global: { plugins: [i18n] } })
    await flushPromises()
    await wrapper.find('input[type=checkbox]').setValue(true)
    expect(form.backup_enabled).toBe(true)
  })

  it('toggling the password eye icon switches the password field to plain text', async () => {
    api.get.mockResolvedValueOnce({ data: [] })
    const wrapper = mountTab({ public_dashboard_enabled: true, public_dashboard_password: 'hunter2' })
    await flushPromises()
    const pwdInput = wrapper.findAll('input').find(i => i.element.value === 'hunter2')
    expect(pwdInput.attributes('type')).toBe('password')
    const eyeBtn = wrapper.findAll('button').find(b => b.find('.fa-eye').exists())
    await eyeBtn.trigger('click')
    expect(pwdInput.attributes('type')).toBe('text')
  })
})
