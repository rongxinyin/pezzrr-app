import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { API_BASE } from '../lib/config'

interface AuthState {
  token: string | null
  role: string | null
  homes: number[]
  login: (username: string, password: string) => Promise<void>
  logout: () => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      role: null,
      homes: [],
      login: async (username, password) => {
        const res = await fetch(`${API_BASE}/auth/login`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username, password }),
        })
        if (!res.ok) throw new Error('Invalid credentials')
        const data = await res.json()
        set({ token: data.access_token, role: data.role, homes: data.homes })
      },
      logout: () => set({ token: null, role: null, homes: [] }),
    }),
    { name: 'pezzrr-auth' },
  ),
)
