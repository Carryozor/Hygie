const UNITS = ['B', 'KiB', 'MiB', 'GiB', 'TiB']

export function formatBytes(bytes) {
  let v = Number(bytes) || 0
  let i = 0
  while (v >= 1024 && i < UNITS.length - 1) { v /= 1024; i++ }
  return `${i ? v.toFixed(1) : v} ${UNITS[i]}`
}
