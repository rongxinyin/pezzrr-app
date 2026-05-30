import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Card } from '../components/Card'
import { Badge } from '../components/Badge'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { apiFetch, ApiError } from '../lib/api'
import type { AdminUser } from '../lib/types'
import type { Status } from '../components/status'

interface HomeOpt {
  home_id: number
  home_name: string
}

const ROLES = ['viewer', 'operator', 'fleet_analyst', 'admin']
const ROLE_STATUS: Record<string, Status> = {
  viewer: 'offline',
  operator: 'info',
  fleet_analyst: 'watch',
  admin: 'act',
}
// Fleet roles see every home, so per-user home grants are irrelevant for them.
const SCOPED_ROLES = ['viewer', 'operator']

interface FormState {
  user_id: number | null
  username: string
  password: string
  role: string
  is_active: boolean
  homes: number[]
}

const EMPTY_FORM: FormState = {
  user_id: null,
  username: '',
  password: '',
  role: 'viewer',
  is_active: true,
  homes: [],
}

const inputStyle = {
  padding: '6px 10px',
  border: '0.5px solid var(--border)',
  background: 'var(--bg-card)',
}
const th = 'pb-2 text-left text-[12px] font-medium uppercase tracking-wide text-text-faint'
const td = 'py-2 text-[13px] text-text'

export function Admin() {
  const qc = useQueryClient()
  const [form, setForm] = useState<FormState | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<AdminUser | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const { data: users } = useQuery({
    queryKey: ['admin-users'],
    queryFn: () => apiFetch<AdminUser[]>('/admin/users'),
  })
  const { data: homes } = useQuery({
    queryKey: ['homes-list'],
    queryFn: () => apiFetch<HomeOpt[]>('/homes'),
  })

  const homeName = (id: number) => homes?.find((h) => h.home_id === id)?.home_name ?? `#${id}`

  function done() {
    setForm(null)
    setErr(null)
    qc.invalidateQueries({ queryKey: ['admin-users'] })
  }
  function fail(e: unknown) {
    setErr(e instanceof ApiError ? e.message : 'Request failed')
  }

  const createUser = useMutation({
    mutationFn: (f: FormState) =>
      apiFetch<AdminUser>('/admin/users', {
        method: 'POST',
        body: JSON.stringify({
          username: f.username,
          password: f.password,
          role: f.role,
          is_active: f.is_active,
          homes: f.homes,
        }),
      }),
    onSuccess: done,
    onError: fail,
  })

  const updateUser = useMutation({
    mutationFn: (f: FormState) =>
      apiFetch<AdminUser>(`/admin/users/${f.user_id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          role: f.role,
          is_active: f.is_active,
          homes: f.homes,
          ...(f.password ? { password: f.password } : {}),
        }),
      }),
    onSuccess: done,
    onError: fail,
  })

  const deleteUser = useMutation({
    mutationFn: (id: number) => apiFetch<void>(`/admin/users/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      setConfirmDelete(null)
      qc.invalidateQueries({ queryKey: ['admin-users'] })
    },
    onError: (e) => {
      setConfirmDelete(null)
      fail(e)
    },
  })

  const editing = form?.user_id != null
  const busy = createUser.isPending || updateUser.isPending
  const scoped = form ? SCOPED_ROLES.includes(form.role) : false

  function submit() {
    if (!form) return
    if (editing) updateUser.mutate(form)
    else createUser.mutate(form)
  }
  function toggleHome(id: number) {
    if (!form) return
    setForm({
      ...form,
      homes: form.homes.includes(id) ? form.homes.filter((h) => h !== id) : [...form.homes, id],
    })
  }

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-[22px] font-medium text-text">Admin · Users</h1>
        <button
          onClick={() => { setForm({ ...EMPTY_FORM }); setErr(null) }}
          className="rounded text-[13px] font-medium"
          style={{ padding: '6px 14px', color: 'var(--bg-card)', background: 'var(--accent)' }}
        >
          New user
        </button>
      </div>

      {err && (
        <div
          className="mb-4 rounded px-4 py-2 text-[13px] text-act"
          style={{ background: 'var(--act-bg)', border: '0.5px solid var(--border)' }}
        >
          {err}
        </div>
      )}

      <Card>
        {users && users.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse">
              <thead>
                <tr style={{ borderBottom: '0.5px solid var(--border)' }}>
                  <th className={th}>User</th>
                  <th className={th}>Role</th>
                  <th className={th}>Active</th>
                  <th className={th}>Homes</th>
                  <th className={th}></th>
                </tr>
              </thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.user_id} style={{ borderBottom: '0.5px solid var(--border)' }}>
                    <td className={td}>{u.username}</td>
                    <td className={td}>
                      <Badge status={ROLE_STATUS[u.role] ?? 'info'}>{u.role}</Badge>
                    </td>
                    <td className={td}>
                      {u.is_active ? (
                        <span className="text-ok">yes</span>
                      ) : (
                        <span className="text-text-faint">disabled</span>
                      )}
                    </td>
                    <td className={`${td} text-text-muted`}>
                      {SCOPED_ROLES.includes(u.role)
                        ? u.homes.length
                          ? u.homes.map(homeName).join(', ')
                          : '—'
                        : 'all'}
                    </td>
                    <td className={`${td} text-right`}>
                      <button
                        onClick={() => {
                          setForm({
                            user_id: u.user_id,
                            username: u.username,
                            password: '',
                            role: u.role,
                            is_active: u.is_active,
                            homes: u.homes,
                          })
                          setErr(null)
                        }}
                        className="text-[13px] text-accent"
                      >
                        Edit
                      </button>
                      <button
                        onClick={() => setConfirmDelete(u)}
                        className="ml-3 text-[13px] text-act"
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="text-[13px] text-text-muted">No users.</div>
        )}
      </Card>

      {form && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center"
          style={{ background: 'rgba(0,0,0,0.4)' }}
          onClick={() => setForm(null)}
        >
          <div
            className="w-full max-w-md rounded-lg"
            style={{ background: 'var(--bg-card)', border: '0.5px solid var(--border)', padding: '20px 24px' }}
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="text-[16px] font-medium text-text">
              {editing ? `Edit ${form.username}` : 'New user'}
            </h2>

            <div className="mt-4 flex flex-col gap-3">
              {!editing && (
                <label className="flex flex-col gap-1 text-[13px] text-text-muted">
                  Username
                  <input
                    value={form.username}
                    onChange={(e) => setForm({ ...form, username: e.target.value })}
                    className="rounded text-[13px] text-text"
                    style={inputStyle}
                  />
                </label>
              )}

              <label className="flex flex-col gap-1 text-[13px] text-text-muted">
                {editing ? 'New password (leave blank to keep)' : 'Password'}
                <input
                  type="password"
                  value={form.password}
                  onChange={(e) => setForm({ ...form, password: e.target.value })}
                  className="rounded text-[13px] text-text"
                  style={inputStyle}
                />
              </label>

              <label className="flex flex-col gap-1 text-[13px] text-text-muted">
                Role
                <select
                  value={form.role}
                  onChange={(e) => setForm({ ...form, role: e.target.value })}
                  className="rounded text-[13px] text-text"
                  style={inputStyle}
                >
                  {ROLES.map((r) => (
                    <option key={r} value={r}>{r}</option>
                  ))}
                </select>
              </label>

              <label className="flex items-center gap-2 text-[13px] text-text">
                <input
                  type="checkbox"
                  checked={form.is_active}
                  onChange={(e) => setForm({ ...form, is_active: e.target.checked })}
                />
                Active
              </label>

              {scoped && (
                <div className="flex flex-col gap-1 text-[13px] text-text-muted">
                  Home access
                  <div
                    className="flex flex-col gap-1 rounded"
                    style={{ border: '0.5px solid var(--border)', padding: '8px 10px', maxHeight: 160, overflowY: 'auto' }}
                  >
                    {(homes ?? []).map((h) => (
                      <label key={h.home_id} className="flex items-center gap-2 text-text">
                        <input
                          type="checkbox"
                          checked={form.homes.includes(h.home_id)}
                          onChange={() => toggleHome(h.home_id)}
                        />
                        {h.home_name}
                      </label>
                    ))}
                  </div>
                </div>
              )}
            </div>

            <div className="mt-5 flex justify-end gap-2">
              <button
                onClick={() => setForm(null)}
                disabled={busy}
                className="rounded text-[13px] text-text-muted"
                style={{ border: '0.5px solid var(--border)', padding: '7px 16px', background: 'var(--bg-card)' }}
              >
                Cancel
              </button>
              <button
                onClick={submit}
                disabled={busy || (!editing && (!form.username.trim() || !form.password))}
                className="rounded text-[13px] font-medium"
                style={{
                  padding: '7px 16px',
                  color: 'var(--bg-card)',
                  background: 'var(--accent)',
                  opacity: busy ? 0.6 : 1,
                }}
              >
                {busy ? 'Saving…' : editing ? 'Save' : 'Create'}
              </button>
            </div>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={confirmDelete != null}
        title="Delete user"
        body={`Permanently delete "${confirmDelete?.username}"? This cannot be undone.`}
        confirmLabel="Delete"
        busyLabel="Deleting…"
        danger
        busy={deleteUser.isPending}
        onConfirm={() => confirmDelete && deleteUser.mutate(confirmDelete.user_id)}
        onCancel={() => setConfirmDelete(null)}
      />
    </div>
  )
}
