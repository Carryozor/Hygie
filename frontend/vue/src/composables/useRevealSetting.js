import api from '@/api/client'

const MASK = '***'

export function isMasked(value) {
  return value === MASK
}

export async function revealSetting(key) {
  const { data } = await api.get(`/settings/reveal/${key}`)
  return data.value
}
