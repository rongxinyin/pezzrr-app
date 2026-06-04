import { useEffect } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Fleet } from './pages/Fleet'
import { Login } from './pages/Login'
import { HomeDetail } from './pages/HomeDetail'
import { Control } from './pages/Control'
import { Scenarios } from './pages/Scenarios'
import { Energy } from './pages/Energy'
import { Reports } from './pages/Reports'
import { Health } from './pages/Health'
import { Admin } from './pages/Admin'
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
            path="/scenarios"
            element={
              <RequireAuth>
                <AppShell>
                  <Scenarios />
                </AppShell>
              </RequireAuth>
            }
          />
          <Route path="/dr" element={<Navigate to="/scenarios" replace />} />
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
            path="/homes/:id/energy"
            element={
              <RequireAuth>
                <AppShell>
                  <Energy />
                </AppShell>
              </RequireAuth>
            }
          />
          <Route
            path="/reports"
            element={
              <RequireAuth>
                <RequireRole allow={['fleet_analyst', 'admin']}>
                  <AppShell>
                    <Reports />
                  </AppShell>
                </RequireRole>
              </RequireAuth>
            }
          />
          <Route
            path="/health"
            element={
              <RequireAuth>
                <AppShell>
                  <Health />
                </AppShell>
              </RequireAuth>
            }
          />
          <Route
            path="/admin"
            element={
              <RequireAuth>
                <RequireRole allow={['admin']}>
                  <AppShell>
                    <Admin />
                  </AppShell>
                </RequireRole>
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
