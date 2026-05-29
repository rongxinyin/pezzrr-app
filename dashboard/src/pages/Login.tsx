import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuthStore } from '../store/auth'
import { Card } from '../components/Card'

export function Login() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const login = useAuthStore((s) => s.login)
  const navigate = useNavigate()

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(username, password)
      navigate('/', { replace: true })
    } catch {
      setError('Invalid username or password.')
    } finally {
      setBusy(false)
    }
  }

  const inputStyle = {
    border: '0.5px solid var(--border)',
    background: 'var(--bg-subtle)',
    padding: '9px 12px',
  }

  return (
    <div className="flex min-h-full items-center justify-center bg-page p-8">
      <div style={{ width: 360 }}>
        <h1 className="mb-1 text-[22px] font-semibold text-text">Pezzrr</h1>
        <p className="mb-5 text-[14px] text-text-muted">Sign in to the fleet dashboard.</p>
        <Card shadow>
          <form onSubmit={submit} className="flex flex-col gap-3">
            <label className="text-[13px] text-text-muted">
              Username
              <input
                className="mt-1 w-full rounded text-text"
                style={inputStyle}
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoFocus
              />
            </label>
            <label className="text-[13px] text-text-muted">
              Password
              <input
                type="password"
                className="mt-1 w-full rounded text-text"
                style={inputStyle}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>
            {error && <div className="text-[13px] text-act">{error}</div>}
            <button
              type="submit"
              disabled={busy || !username || !password}
              className="mt-1 rounded text-[14px] font-medium"
              style={{ background: 'var(--accent)', color: '#fff', padding: '9px 12px', opacity: busy ? 0.6 : 1 }}
            >
              {busy ? 'Signing in…' : 'Sign in'}
            </button>
          </form>
        </Card>
      </div>
    </div>
  )
}
