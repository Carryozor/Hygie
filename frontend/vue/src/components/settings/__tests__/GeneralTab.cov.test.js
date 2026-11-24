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
    prometheus_bearer_token: '',
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
    const delBtn = wrapper.find('[data-test="backup-delete"]')
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
    const delBtn = wrapper.find('[data-test="backup-delete"]')
    await delBtn.trigger('click')
    await flushPromises()
    expect(api.delete).toHaveBeenCalledWith(`/backup/${encodeURIComponent('backup 2026-01-01.sql')}`)
  })

  it('leaves the backup list unchanged when deletion fails', async () => {
    api.get.mockResolvedValueOnce({ data: [{ filename: 'old.sql', size_mb: 2 }] })
    const wrapper = mountTab()
    await flushPromises()
    api.delete.mockRejectedValueOnce(new Error('locked'))
    const delBtn = wrapper.find('[data-test="backup-delete"]')
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

  describe('backup restore check', () => {
    function withBackups() {
      api.get.mockImplementation(async (url) => {
        if (url === '/backup') return { data: [{ filename: 'hygie_20261031_205800.sql.gz', size_mb: 1 }] }
        return { data: { state: 'ok' } }
      })
    }

    it('posts to the verify endpoint of the clicked backup and shows a success', async () => {
      withBackups()
      api.post.mockResolvedValueOnce({ data: { ok: true, format: 'sql', reason: null } })
      const wrapper = mountTab()
      await flushPromises()
      await wrapper.find('[data-test="backup-verify"]').trigger('click')
      await flushPromises()
      expect(api.post).toHaveBeenCalledWith('/backup/hygie_20261031_205800.sql.gz/verify')
      expect(wrapper.find('[data-test="backup-verify-ok"]').exists()).toBe(true)
      expect(wrapper.find('[data-test="backup-verify-bad"]').exists()).toBe(false)
    })

    it('shows the reason when the backup does not look restorable', async () => {
      withBackups()
      api.post.mockResolvedValueOnce({ data: { ok: false, format: 'sql', reason: 'dump incomplet' } })
      const wrapper = mountTab()
      await flushPromises()
      await wrapper.find('[data-test="backup-verify"]').trigger('click')
      await flushPromises()
      const bad = wrapper.find('[data-test="backup-verify-bad"]')
      expect(bad.exists()).toBe(true)
      expect(bad.text()).toContain('dump incomplet')
    })

    it('reports a failed request instead of staying silent', async () => {
      withBackups()
      api.post.mockRejectedValueOnce(new Error('500'))
      const wrapper = mountTab()
      await flushPromises()
      await wrapper.find('[data-test="backup-verify"]').trigger('click')
      await flushPromises()
      expect(wrapper.find('[data-test="backup-verify-bad"]').exists()).toBe(true)
    })
  })

  describe('backup health banner', () => {
    function routeGet(status) {
      api.get.mockImplementation(async (url) => {
        if (url === '/backup/status') return { data: status }
        return { data: [] }
      })
    }

    it('warns with the age of the last dump when the backup chain is stale', async () => {
      routeGet({ state: 'stale', age_hours: 648, interval_hours: 24, last_backup_at: '2026-10-04T20:58:00+00:00' })
      const wrapper = mountTab()
      await flushPromises()
      const banner = wrapper.find('[data-test="backup-stale"]')
      expect(banner.exists()).toBe(true)
      expect(banner.text()).toContain('648')
    })

    it('stays hidden when the last dump is recent', async () => {
      routeGet({ state: 'ok', age_hours: 3, interval_hours: 24, last_backup_at: '2026-10-31T20:58:00+00:00' })
      const wrapper = mountTab()
      await flushPromises()
      expect(wrapper.find('[data-test="backup-stale"]').exists()).toBe(false)
    })

    it('states that no dump exists yet when the folder is empty', async () => {
      routeGet({ state: 'never', age_hours: null, interval_hours: 24, last_backup_at: null })
      const wrapper = mountTab()
      await flushPromises()
      expect(wrapper.find('[data-test="backup-never"]').exists()).toBe(true)
    })

    it('does not break the tab when the status endpoint fails', async () => {
      api.get.mockImplementation(async (url) => {
        if (url === '/backup/status') throw new Error('500')
        return { data: [{ filename: 'b.sql', size_mb: 1 }] }
      })
      const wrapper = mountTab()
      await flushPromises()
      expect(wrapper.text()).toContain('b.sql')
      expect(wrapper.find('[data-test="backup-stale"]').exists()).toBe(false)
    })

    it('refreshes the status after a manual backup', async () => {
      let state = { state: 'stale', age_hours: 700, interval_hours: 24 }
      api.get.mockImplementation(async (url) => (url === '/backup/status' ? { data: state } : { data: [] }))
      api.post.mockResolvedValueOnce({ data: { filename: 'hygie_new.sql.gz' } })
      const wrapper = mountTab()
      await flushPromises()
      expect(wrapper.find('[data-test="backup-stale"]').exists()).toBe(true)
      state = { state: 'ok', age_hours: 0, interval_hours: 24 }
      const btn = wrapper.findAll('button').find(b => b.text().includes(i18n.global.t('settings.general.backup.manual')))
      await btn.trigger('click')
      await flushPromises()
      expect(wrapper.find('[data-test="backup-stale"]').exists()).toBe(false)
    })
  })
  it('binds the Prometheus bearer token field to form.prometheus_bearer_token', async () => {
    api.get.mockResolvedValueOnce({ data: [] })
    const form = baseForm()
    const wrapper = mount(GeneralTab, { props: { form }, global: { plugins: [i18n] } })
    await flushPromises()
    const input = wrapper.find('[data-test="prometheus-token"]')
    expect(input.attributes('type')).toBe('password')
    await input.setValue('tok123')
    expect(form.prometheus_bearer_token).toBe('tok123')
  })
})
