import { useEffect } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Fleet } from './pages/Fleet'
import { Login } from './pages/Login'
import { HomeDetail } from './pages/HomeDetail'
import { Control } from './pages/Control'
import { Dr } from './pages/Dr'
import { Showcase } from './pages/Showcase'
import { AppShell } from './components/AppShell'
import { RequireAuth } from './components/RequireAuth'
import { RequireRole } from './components/RequireRole'
import { useUIStore } from './store/ui'

const queryClient = new QueryClient()

function App() {
  const theme = useUIStore((s) => s.theme)
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
  }, [theme])

  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/showcase" element={<Showcase />} />
          <Route
            path="/"
            element={
              <RequireAuth>
                <AppShell>
                  <Fleet />
                </AppShell>
              </RequireAuth>
            }
          />
          <Route
            path="/dr"
            element={
              <RequireAuth>
                <AppShell>
                  <Dr />
                </AppShell>
              </RequireAuth>
            }
          />
          <Route
            path="/homes/:id"
            element={
              <RequireAuth>
                <AppShell>
                  <HomeDetail />
                </AppShell>
              </RequireAuth>
            }
          />
          <Route
            path="/control/:id"
            element={
              <RequireAuth>
                <RequireRole allow={['operator', 'admin']}>
                  <AppShell>
                    <Control />
                  </AppShell>
                </RequireRole>
              </RequireAuth>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}

export default App
