export type Status = 'ok' | 'watch' | 'act' | 'info' | 'offline'

// CSS-variable references for each status family (foreground + soft background).
export const STATUS_COLORS: Record<Status, { fg: string; bg: string }> = {
  ok: { fg: 'var(--ok)', bg: 'var(--ok-bg)' },
  watch: { fg: 'var(--watch)', bg: 'var(--watch-bg)' },
  act: { fg: 'var(--act)', bg: 'var(--act-bg)' },
  info: { fg: 'var(--info)', bg: 'var(--info-bg)' },
  offline: { fg: 'var(--text-faint)', bg: 'var(--bg-subtle)' },
}

export type CircuitPriority = 'critical' | 'essential' | 'non_essential'

// Circuit shed-priority tiers map onto the status palette: critical (never
// shed) = act/red, essential (nice-to-have) = watch/amber, non-essential
// (shed first) = ok/green.
export const PRIORITY_STATUS: Record<CircuitPriority, Status> = {
  critical: 'act',
  essential: 'watch',
  non_essential: 'ok',
}

export const PRIORITY_LABEL: Record<CircuitPriority, string> = {
  critical: 'critical',
  essential: 'essential',
  non_essential: 'non-essential',
}
