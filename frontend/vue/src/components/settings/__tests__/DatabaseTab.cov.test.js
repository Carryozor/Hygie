// src/components/settings/__tests__/DatabaseTab.cov.test.js
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { i18n } from '@/i18n'

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), post: vi.fn() } }))
import api from '@/api/client'
import DatabaseTab from '../DatabaseTab.vue'

function mountTab() {
  return mount(DatabaseTab, { global: { plugins: [i18n] } })
}

describe('DatabaseTab', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.get.mockResolvedValue({ data: { status: 'success' } }) // default: no migration running
  })
  afterEach(() => vi.useRealTimers())

  it('fetches DB info on mount and shows the dialect badge', async () => {
    api.get.mockResolvedValueOnce({ data: { dialect: 'sqlite', connection: '/app/data/hygie.db', tables: { rules: 3 } } })
    const wrapper = mountTab()
    await flushPromises()
    expect(wrapper.text()).toContain('SQLite')
    expect(wrapper.text()).toContain('rules')
  })

  it('auto-selects mariadb_to_sqlite direction when already on MariaDB', async () => {
    api.get.mockResolvedValueOnce({ data: { dialect: 'mariadb', connection: 'mariadb://x', tables: {} } })
    const wrapper = mountTab()
    await flushPromises()
    const activeBtn = wrapper.findAll('button').find(b => b.classes().includes('text-[var(--accent)]'))
    expect(activeBtn.text()).toBe(i18n.global.t('settings.database.toSqlite'))
  })

  it('disables the "to MariaDB" direction option while already on MariaDB', async () => {
    api.get.mockResolvedValueOnce({ data: { dialect: 'mariadb', connection: 'x', tables: {} } })
    const wrapper = mountTab()
    await flushPromises()
    const toMariadbBtn = wrapper.findAll('button').find(b => b.text() === i18n.global.t('settings.database.toMariadb'))
    expect(toMariadbBtn.attributes('disabled')).toBeDefined()
  })

  it('testConnection posts the target URL and shows a success result', async () => {
    api.get.mockResolvedValueOnce({ data: { dialect: 'sqlite', connection: 'x', tables: {} } })
    const wrapper = mountTab()
    await flushPromises()
    await wrapper.find('input[type=text]').setValue('mysql://user:pass@host/db')
    api.post.mockResolvedValueOnce({ data: { ok: true, message: 'Connexion OK' } })
    const testBtn = wrapper.findAll('button').find(b => b.text().includes(i18n.global.t('settings.database.testConn')))
    await testBtn.trigger('click')
    await flushPromises()
    expect(api.post).toHaveBeenCalledWith('/database/test', { url: 'mysql://user:pass@host/db' })
    expect(wrapper.text()).toContain('Connexion OK')
  })

  it('testConnection shows the server-provided error detail on failure', async () => {
    api.get.mockResolvedValueOnce({ data: { dialect: 'sqlite', connection: 'x', tables: {} } })
    const wrapper = mountTab()
    await flushPromises()
    await wrapper.find('input[type=text]').setValue('bad-url')
    api.post.mockRejectedValueOnce({ response: { data: { detail: 'DNS échoué' } } })
    const testBtn = wrapper.findAll('button').find(b => b.text().includes(i18n.global.t('settings.database.testConn')))
    await testBtn.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('DNS échoué')
  })

  it('the test-connection button is disabled while targetUrl is empty', async () => {
    api.get.mockResolvedValueOnce({ data: { dialect: 'sqlite', connection: 'x', tables: {} } })
    const wrapper = mountTab()
    await flushPromises()
    const testBtn = wrapper.findAll('button').find(b => b.text().includes(i18n.global.t('settings.database.testConn')))
    expect(testBtn.attributes('disabled')).toBeDefined()
  })

  it('startMigration is blocked (canMigrate=false) when direction=sqlite_to_mariadb and targetUrl is blank', async () => {
    api.get.mockResolvedValueOnce({ data: { dialect: 'sqlite', connection: 'x', tables: {} } })
    const wrapper = mountTab()
    await flushPromises()
    const startBtn = wrapper.findAll('button').find(b => b.text().includes(i18n.global.t('settings.database.startMigration')))
    expect(startBtn.attributes('disabled')).toBeDefined()
    await startBtn.trigger('click')
    expect(api.post).not.toHaveBeenCalled()
  })

  it('startMigration posts the payload and begins polling for status', async () => {
    vi.useFakeTimers()
    api.get.mockResolvedValueOnce({ data: { dialect: 'sqlite', connection: 'x', tables: {} } })
    const wrapper = mountTab()
    await flushPromises()
    await wrapper.find('input[type=text]').setValue('mysql://user:pass@host/db')
    api.post.mockResolvedValueOnce({ data: { status: 'started' } })
    const startBtn = wrapper.findAll('button').find(b => b.text().includes(i18n.global.t('settings.database.startMigration')))
    await startBtn.trigger('click')
    await flushPromises()
    expect(api.post).toHaveBeenCalledWith('/database/migrate', expect.objectContaining({
      direction: 'sqlite_to_mariadb', target_url: 'mysql://user:pass@host/db', dry_run: false,
    }))
    api.get.mockResolvedValueOnce({ data: { status: null } }) // still running
    await vi.advanceTimersByTimeAsync(2000)
    await flushPromises()
    expect(api.get).toHaveBeenCalledWith('/database/migrate/status')
  })

  it('startMigration surfaces "already_running" as a global error event and resets the migrating flag', async () => {
    api.get.mockResolvedValueOnce({ data: { dialect: 'sqlite', connection: 'x', tables: {} } })
    const wrapper = mountTab()
    await flushPromises()
    await wrapper.find('input[type=text]').setValue('mysql://x')
    api.post.mockResolvedValueOnce({ data: { status: 'already_running' } })
    const listener = vi.fn()
    window.addEventListener('hygie:error', listener)
    const startBtn = wrapper.findAll('button').find(b => b.text().includes(i18n.global.t('settings.database.startMigration')))
    await startBtn.trigger('click')
    await flushPromises()
    expect(listener).toHaveBeenCalledTimes(1)
    expect(startBtn.text()).toContain(i18n.global.t('settings.database.startMigration'))
    window.removeEventListener('hygie:error', listener)
  })

  it('startMigration shows the error status and stops migrating on API rejection', async () => {
    api.get.mockResolvedValueOnce({ data: { dialect: 'sqlite', connection: 'x', tables: {} } })
    const wrapper = mountTab()
    await flushPromises()
    await wrapper.find('input[type=text]').setValue('mysql://x')
    api.post.mockRejectedValueOnce({ response: { data: { error: 'Connexion refusée' } } })
    const startBtn = wrapper.findAll('button').find(b => b.text().includes(i18n.global.t('settings.database.startMigration')))
    await startBtn.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Connexion refusée')
  })

  it('shows the restart hint with DATABASE_URL after a successful non-dry-run sqlite_to_mariadb migration', async () => {
    vi.useFakeTimers()
    api.get.mockResolvedValueOnce({ data: { dialect: 'sqlite', connection: 'x', tables: {} } })
    const wrapper = mountTab()
    await flushPromises()
    await wrapper.find('input[type=text]').setValue('mysql://real-target')
    api.post.mockResolvedValueOnce({ data: { status: 'started' } })
    const startBtn = wrapper.findAll('button').find(b => b.text().includes(i18n.global.t('settings.database.startMigration')))
    await startBtn.trigger('click')
    await flushPromises()
    api.get.mockResolvedValueOnce({ data: { status: 'success', message: 'ok' } })
    await vi.advanceTimersByTimeAsync(2000)
    await flushPromises()
    expect(wrapper.text()).toContain('DATABASE_URL=mysql://real-target')
  })

  it('resuming with an in-progress migration on mount (status.status is falsy) starts polling immediately', async () => {
    vi.useFakeTimers()
    api.get.mockResolvedValueOnce({ data: { dialect: 'sqlite', connection: 'x', tables: {} } }) // fetchInfo
    api.get.mockResolvedValueOnce({ data: { status: null } }) // fetchJobStatus on mount: still running
    const wrapper = mountTab()
    await flushPromises()
    expect(wrapper.text()).toContain(i18n.global.t('settings.database.migrating'))
    api.get.mockClear()
    api.get.mockResolvedValueOnce({ data: { status: 'success' } })
    await vi.advanceTimersByTimeAsync(2000)
    await flushPromises()
    expect(api.get).toHaveBeenCalledWith('/database/migrate/status')
  })

  it('resetStatus clears jobStatus/testResult when the migration direction is switched', async () => {
    api.get.mockResolvedValueOnce({ data: { dialect: 'sqlite', connection: 'x', tables: {} } })
    const wrapper = mountTab()
    await flushPromises()
    await wrapper.find('input[type=text]').setValue('bad')
    api.post.mockRejectedValueOnce({ response: { data: { detail: 'err' } } })
    const testBtn = wrapper.findAll('button').find(b => b.text().includes(i18n.global.t('settings.database.testConn')))
    await testBtn.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('err')
    const directionBtn = wrapper.findAll('button').find(b => b.text() === i18n.global.t('settings.database.toMariadb'))
    await directionBtn.trigger('click')
    expect(wrapper.text()).not.toContain('err')
  })
})
