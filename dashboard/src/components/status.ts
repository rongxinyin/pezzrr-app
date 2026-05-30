export type Status = 'ok' | 'watch' | 'act' | 'info' | 'offline'

// CSS-variable references for each status family (foreground + soft background).
export const STATUS_COLORS: Record<Status, { fg: string; bg: string }> = {
  ok: { fg: 'var(--ok)', bg: 'var(--ok-bg)' },
  watch: { fg: 'var(--watch)', bg: 'var(--watch-bg)' },
  act: { fg: 'var(--act)', bg: 'var(--act-bg)' },
  info: { fg: 'var(--info)', bg: 'var(--info-bg)' },
  offline: { fg: 'var(--text-faint)', bg: 'var(--bg-subtle)' },
}
