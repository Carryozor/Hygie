// src/views/__tests__/RulesView.cov.test.js
//
// RulesView's destructive action is rule deletion (simple + expert), routed
// through runConfirmedAction like Queue/Ignored. CreateRuleModal is stubbed
// (it's a large standalone component out of this file's scope) — we only
// assert the props RulesView feeds it and the 'saved' contract it depends on.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { DOMWrapper } from '@vue/test-utils'
import { mountView, flushAll } from './testUtils.cov.js'

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))

import api from '@/api/client'
import RulesView from '../RulesView.vue'

const CreateRuleModalStub = {
  props: ['open', 'editRule', 'editType'],
  emits: ['close', 'saved'],
  template: '<div class="create-rule-modal-stub" />',
}

const SIMPLE_RULE = { id: 1, seerr_username: 'alice', library_id: 10, grace_days: 7, enabled: true }
const EXPERT_RULE = { id: 2, name: 'Old & unwatched', enabled: false, action: 'queue', grace_days: 14, condition_groups: [{ conditions: [1, 2] }], operator: 'AND' }

function mockGets({ simple = [], expert = [], libraries = [], servers = [] } = {}) {
  api.get.mockImplementation(url => {
    if (url === '/seerr-rules') return Promise.resolve({ data: simple })
    if (url === '/expert-rules') return Promise.resolve({ data: expert })
    if (url === '/settings/media-servers') return Promise.resolve({ data: servers })
    if (url === '/libraries') return Promise.resolve({ data: libraries })
    return Promise.resolve({ data: {} })
  })
}

async function mountRules(opts) {
  mockGets(opts)
  return mountView(RulesView, { path: '/rules', stubs: { CreateRuleModal: CreateRuleModalStub } })
}

describe('RulesView', () => {
  let errorEvents, errorListener
  beforeEach(() => {
    vi.clearAllMocks()
    errorEvents = []
    errorListener = e => errorEvents.push(e.detail)
    window.addEventListener('hygie:error', errorListener)
  })
  afterEach(() => {
    window.removeEventListener('hygie:error', errorListener)
    // See QueueView.cov.test.js — Teleport(to: 'body') content outlives the
    // wrapper and must be cleared to avoid leaking between tests.
    document.body.innerHTML = ''
  })

  it('shows the empty-state copy for both sections when there are no rules', async () => {
    const { wrapper } = await mountRules({})
    expect(wrapper.text()).toContain('Aucune règle simple.')
    expect(wrapper.text()).toContain('Aucune règle experte.')
  })

  it('renders simple and expert rules once fetched', async () => {
    const { wrapper } = await mountRules({ simple: [SIMPLE_RULE], expert: [EXPERT_RULE] })
    expect(wrapper.text()).toContain('alice')
    expect(wrapper.text()).toContain('Old & unwatched')
  })

  it('does not delete a simple rule when the confirmation is cancelled', async () => {
    const { wrapper } = await mountRules({ simple: [SIMPLE_RULE] })
    await wrapper.find('button[title="Supprimer"]').trigger('click')
    await flushAll()
    const cancelBtn = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Annuler')
    cancelBtn.click()
    await flushAll()
    expect(api.delete).not.toHaveBeenCalled()
  })

  it('deletes the confirmed simple rule via DELETE /seerr-rules/{id}', async () => {
    api.delete.mockResolvedValue({ data: {} })
    const { wrapper } = await mountRules({ simple: [SIMPLE_RULE] })
    await wrapper.find('button[title="Supprimer"]').trigger('click')
    await flushAll()
    const confirmBtn = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Supprimer')
    confirmBtn.click()
    await flushAll()
    expect(api.delete).toHaveBeenCalledWith('/seerr-rules/1')
    expect(wrapper.text()).not.toContain('alice')
  })

  it('deletes the confirmed expert rule via DELETE /expert-rules/{id}, not the simple endpoint', async () => {
    api.delete.mockResolvedValue({ data: {} })
    const { wrapper } = await mountRules({ expert: [EXPERT_RULE] })
    await wrapper.find('button[title="Supprimer"]').trigger('click')
    await flushAll()
    const confirmBtn = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Supprimer')
    confirmBtn.click()
    await flushAll()
    expect(api.delete).toHaveBeenCalledWith('/expert-rules/2')
    expect(api.delete).not.toHaveBeenCalledWith('/seerr-rules/2')
  })

  it('a failed rule deletion surfaces an error and keeps the confirm modal open', async () => {
    api.delete.mockRejectedValue({ response: { status: 403, data: {} } })
    const { wrapper } = await mountRules({ simple: [SIMPLE_RULE] })
    await wrapper.find('button[title="Supprimer"]').trigger('click')
    await flushAll()
    const confirmBtn = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Supprimer')
    confirmBtn.click()
    await flushAll()
    expect(errorEvents.length).toBe(1)
    expect(errorEvents[0].message).toContain('Impossible de supprimer la règle')
    expect(document.body.querySelector('.fixed.inset-0')).not.toBeNull()
    // Rule must still be present — the delete did NOT go through.
    expect(wrapper.text()).toContain('alice')
  })

  it('toggling a simple rule flips only `enabled` via PUT /seerr-rules/{id}', async () => {
    api.put.mockResolvedValue({ data: { ...SIMPLE_RULE, enabled: false } })
    const { wrapper } = await mountRules({ simple: [SIMPLE_RULE] })
    await wrapper.find('button[title="Désactiver"]').trigger('click')
    await flushAll()
    expect(api.put).toHaveBeenCalledWith('/seerr-rules/1', { ...SIMPLE_RULE, enabled: false })
  })

  it('toggling an expert rule PUTs the inverted enabled flag to /expert-rules/{id}', async () => {
    api.put.mockResolvedValue({ data: { ...EXPERT_RULE, enabled: true } })
    const { wrapper } = await mountRules({ expert: [EXPERT_RULE] })
    // EXPERT_RULE.enabled is false -> toggle button text is "Inactif" (no title attr on this one)
    const toggleBtn = [...wrapper.findAll('button')].find(b => b.text() === 'Inactif')
    await toggleBtn.trigger('click')
    await flushAll()
    expect(api.put).toHaveBeenCalledWith('/expert-rules/2', { ...EXPERT_RULE, enabled: true })
  })

  it('runRule POSTs to /libraries/{id}/scan for a simple rule\'s library', async () => {
    api.post.mockResolvedValue({ data: {} })
    const { wrapper } = await mountRules({ simple: [SIMPLE_RULE] })
    await wrapper.find('button[title="Lancer un scan"]').trigger('click')
    await flushAll()
    expect(api.post).toHaveBeenCalledWith('/libraries/10/scan')
  })

  it('a 409 (scan already running) surfaces a translated "scan already running" toast', async () => {
    api.post.mockRejectedValue({ response: { status: 409 } })
    const { wrapper } = await mountRules({ simple: [SIMPLE_RULE] })
    await wrapper.find('button[title="Lancer un scan"]').trigger('click')
    await flushAll()
    expect(errorEvents.length).toBe(1)
    expect(errorEvents[0].message).toBe('Un scan est déjà en cours.')
  })

  it('migrateFromLibraries shows the success message with the created count', async () => {
    api.post.mockResolvedValue({ data: { created: 3 } })
    mockGets({})
    api.get.mockImplementation(url => {
      if (url === '/seerr-rules') return Promise.resolve({ data: [] })
      if (url === '/expert-rules') return Promise.resolve({ data: [] })
      if (url === '/settings/media-servers') return Promise.resolve({ data: [] })
      if (url === '/libraries') return Promise.resolve({ data: [] })
      return Promise.resolve({ data: {} })
    })
    const { wrapper } = await mountView(RulesView, { path: '/rules', stubs: { CreateRuleModal: CreateRuleModalStub } })
    const migrateBtn = [...wrapper.findAll('button')].find(b => b.text().includes('Migrer depuis bibliothèques'))
    await migrateBtn.trigger('click')
    await flushAll()
    expect(api.post).toHaveBeenCalledWith('/expert-rules/migrate-from-libraries')
    expect(wrapper.text()).toContain('3 règle(s) créée(s) avec succès.')
  })

  it('migrateFromLibraries shows the error message on failure', async () => {
    api.post.mockRejectedValue(new Error('boom'))
    const { wrapper } = await mountRules({})
    const migrateBtn = [...wrapper.findAll('button')].find(b => b.text().includes('Migrer depuis bibliothèques'))
    await migrateBtn.trigger('click')
    await flushAll()
    expect(wrapper.text()).toContain('Erreur lors de la migration.')
  })

  it('opening "Nouvelle règle" passes open=true, no rule, and an empty type to the modal', async () => {
    const { wrapper } = await mountRules({})
    await wrapper.find('button').trigger('click') // first button in header = "Nouvelle règle"
    const modal = wrapper.findComponent(CreateRuleModalStub)
    expect(modal.props('open')).toBe(true)
    expect(modal.props('editRule')).toBeNull()
    expect(modal.props('editType')).toBe('')
  })

  it('editing a simple rule passes a copy of the rule and type "simple" to the modal', async () => {
    const { wrapper } = await mountRules({ simple: [SIMPLE_RULE] })
    await wrapper.find('button[title="Modifier"]').trigger('click')
    const modal = wrapper.findComponent(CreateRuleModalStub)
    expect(modal.props('editType')).toBe('simple')
    expect(modal.props('editRule')).toEqual(SIMPLE_RULE)
    expect(modal.props('editRule')).not.toBe(SIMPLE_RULE) // must be a copy, not the store's own object
  })

  it('onSaved with an existing simple rule id calls updateSimpleRule via PUT, not createSimpleRule', async () => {
    api.put.mockResolvedValue({ data: { ...SIMPLE_RULE, grace_days: 3 } })
    const { wrapper } = await mountRules({ simple: [SIMPLE_RULE] })
    await wrapper.find('button[title="Modifier"]').trigger('click')
    const modal = wrapper.findComponent(CreateRuleModalStub)
    const done = vi.fn()
    modal.vm.$emit('saved', { type: 'simple', data: { grace_days: 3 }, done })
    await flushAll()
    expect(api.put).toHaveBeenCalledWith('/seerr-rules/1', { grace_days: 3 })
    expect(api.post).not.toHaveBeenCalled()
    expect(done).toHaveBeenCalledTimes(1)
  })

  it('onSaved for a brand-new simple rule (no id) calls createSimpleRule via POST', async () => {
    api.post.mockResolvedValue({ data: { id: 99 } })
    const { wrapper } = await mountRules({})
    await wrapper.find('button').trigger('click') // "Nouvelle règle"
    const modal = wrapper.findComponent(CreateRuleModalStub)
    const done = vi.fn()
    modal.vm.$emit('saved', { type: 'simple', data: { seerr_username: 'bob' }, done })
    await flushAll()
    expect(api.post).toHaveBeenCalledWith('/seerr-rules', { seerr_username: 'bob' })
    expect(done).toHaveBeenCalledTimes(1)
  })

  it('onSaved still calls done() and keeps the modal open when the save fails', async () => {
    api.post.mockRejectedValue({ response: { status: 422, data: {} } })
    const { wrapper } = await mountRules({})
    await wrapper.find('button').trigger('click')
    const modal = wrapper.findComponent(CreateRuleModalStub)
    const done = vi.fn()
    modal.vm.$emit('saved', { type: 'simple', data: {}, done })
    await flushAll()
    expect(done).toHaveBeenCalledTimes(1)
    expect(wrapper.findComponent(CreateRuleModalStub).props('open')).toBe(true)
  })

  it('the modal\'s close event hides it without saving anything', async () => {
    const { wrapper } = await mountRules({})
    await wrapper.find('button').trigger('click') // "Nouvelle règle" -> modal.open = true
    expect(wrapper.findComponent(CreateRuleModalStub).props('open')).toBe(true)
    wrapper.findComponent(CreateRuleModalStub).vm.$emit('close')
    await flushAll()
    expect(wrapper.findComponent(CreateRuleModalStub).props('open')).toBe(false)
    expect(api.post).not.toHaveBeenCalled()
  })

  it('onSaved with an existing expert rule id calls updateExpertRule via PUT /expert-rules/{id}', async () => {
    api.put.mockResolvedValue({ data: { ...EXPERT_RULE, name: 'Renamed' } })
    const { wrapper } = await mountRules({ expert: [EXPERT_RULE] })
    await wrapper.find('button[title="Modifier"]').trigger('click')
    const modal = wrapper.findComponent(CreateRuleModalStub)
    const done = vi.fn()
    modal.vm.$emit('saved', { type: 'expert', data: { name: 'Renamed' }, done })
    await flushAll()
    expect(api.put).toHaveBeenCalledWith('/expert-rules/2', { name: 'Renamed' })
    expect(done).toHaveBeenCalledTimes(1)
  })

  it('onSaved for a brand-new expert rule (no id) calls createExpertRule via POST /expert-rules', async () => {
    api.post.mockResolvedValue({ data: { id: 55 } })
    const { wrapper } = await mountRules({})
    await wrapper.find('button').trigger('click') // "Nouvelle règle"
    const modal = wrapper.findComponent(CreateRuleModalStub)
    const done = vi.fn()
    modal.vm.$emit('saved', { type: 'expert', data: { name: 'Brand new' }, done })
    await flushAll()
    expect(api.post).toHaveBeenCalledWith('/expert-rules', { name: 'Brand new' })
    expect(done).toHaveBeenCalledTimes(1)
  })

  it('cloning a simple rule opens the modal with the rule\'s id stripped', async () => {
    const { wrapper } = await mountRules({ simple: [SIMPLE_RULE] })
    await wrapper.find('button[title="Cloner"]').trigger('click')
    const modal = wrapper.findComponent(CreateRuleModalStub)
    expect(modal.props('editType')).toBe('simple')
    expect(modal.props('editRule')).toEqual({ seerr_username: 'alice', library_id: 10, grace_days: 7, enabled: true })
    expect(modal.props('editRule').id).toBeUndefined()
  })

  it('cloning an expert rule suffixes the name and deep-clones conditions (not the store\'s array)', async () => {
    const withConditions = { ...EXPERT_RULE, conditions: [{ field: 'unwatched_days', op: '>', value: 30 }] }
    const { wrapper } = await mountRules({ expert: [withConditions] })
    await wrapper.find('button[title="Cloner"]').trigger('click')
    const modal = wrapper.findComponent(CreateRuleModalStub)
    expect(modal.props('editType')).toBe('expert')
    expect(modal.props('editRule').name).toBe('Old & unwatched (copie)')
    expect(modal.props('editRule').id).toBeUndefined()
    expect(modal.props('editRule').conditions).toEqual(withConditions.conditions)
    expect(modal.props('editRule').conditions).not.toBe(withConditions.conditions)
  })

  it('editing an expert rule deep-clones its conditions array', async () => {
    const withConditions = { ...EXPERT_RULE, conditions: [{ field: 'rating', op: '<', value: 5 }] }
    const { wrapper } = await mountRules({ expert: [withConditions] })
    await wrapper.find('button[title="Modifier"]').trigger('click')
    const modal = wrapper.findComponent(CreateRuleModalStub)
    expect(modal.props('editType')).toBe('expert')
    expect(modal.props('editRule').conditions).toEqual(withConditions.conditions)
    expect(modal.props('editRule').conditions).not.toBe(withConditions.conditions)
  })

  it('runRule for an expert rule with multiple library_ids POSTs to /libraries/scan-multi', async () => {
    api.post.mockResolvedValue({ data: {} })
    const multi = { ...EXPERT_RULE, library_ids: [10, 20] }
    const { wrapper } = await mountRules({ expert: [multi] })
    await wrapper.find('button[title="Lancer un scan"]').trigger('click')
    await flushAll()
    expect(api.post).toHaveBeenCalledWith('/libraries/scan-multi', { library_ids: [10, 20] })
  })

  it('a non-409 scan failure is reported via the generic formatApiError toast', async () => {
    api.post.mockRejectedValue({ response: { status: 500, data: { detail: 'boom' } } })
    const { wrapper } = await mountRules({ simple: [SIMPLE_RULE] })
    await wrapper.find('button[title="Lancer un scan"]').trigger('click')
    await flushAll()
    expect(errorEvents.length).toBe(1)
    expect(errorEvents[0].message).toBe('boom') // formatApiError(500) returns data.detail when present
  })

  it('renders the notify-only and raw fallback action labels for expert rules', async () => {
    const notifyOnly = { ...EXPERT_RULE, id: 3, action: 'notify_only' }
    const unknown = { ...EXPERT_RULE, id: 4, action: 'some_future_action' }
    const { wrapper } = await mountRules({ expert: [notifyOnly, unknown] })
    expect(wrapper.text()).toContain('Notifier seulement')
    expect(wrapper.text()).toContain('some_future_action')
  })

  it('falls back to summing bare `conditions` when a rule has no condition_groups', async () => {
    const flat = { ...EXPERT_RULE, id: 5, condition_groups: undefined, conditions: [1, 2, 3] }
    const { wrapper } = await mountRules({ expert: [flat] })
    expect(wrapper.text()).toContain('3 condition(s)')
  })

  it('shows the raw library id when it does not match any known library', async () => {
    const { wrapper } = await mountRules({ simple: [SIMPLE_RULE], libraries: [] })
    expect(wrapper.text()).toContain('10') // SIMPLE_RULE.library_id, unresolved
  })

  it('clicking the delete-confirmation backdrop cancels without deleting', async () => {
    const { wrapper } = await mountRules({ simple: [SIMPLE_RULE] })
    await wrapper.find('button[title="Supprimer"]').trigger('click')
    await flushAll()
    const backdrop = document.body.querySelector('.fixed.inset-0')
    expect(backdrop).not.toBeNull()
    await new DOMWrapper(backdrop).trigger('mousedown')
    await flushAll()
    expect(document.body.querySelector('.fixed.inset-0')).toBeNull()
    expect(api.delete).not.toHaveBeenCalled()
  })
})
