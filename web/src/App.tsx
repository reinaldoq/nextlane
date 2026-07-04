import { useEffect, useState } from 'react'
import { Navigate, NavLink, Outlet, Route, Routes } from 'react-router-dom'
import { Button, Flex, Layout, Spin, Typography, theme } from 'antd'
import type { Session } from '@supabase/supabase-js'
import { supabase } from './lib/supabase'
import LoginPage from './pages/LoginPage'
import InventoryPage from './pages/InventoryPage'
import MissionControlPage from './pages/MissionControlPage'
import ReportIssueModal from './components/ReportIssueModal'

const { Header, Content } = Layout
const { Title, Text } = Typography

type SessionState =
  | { status: 'loading' }
  | { status: 'authenticated'; session: Session }
  | { status: 'anonymous' }

/** Tracks the current Supabase session: initial async check + live updates. */
function useSessionState(): SessionState {
  const [state, setState] = useState<SessionState>({ status: 'loading' })

  useEffect(() => {
    let active = true

    // Only resolves the initial "loading" state: if onAuthStateChange has
    // already delivered a session (e.g. INITIAL_SESSION), keep that result.
    supabase.auth
      .getSession()
      .then(({ data }) => {
        if (!active) return
        setState((prev) => {
          if (prev.status !== 'loading') return prev
          return data.session
            ? { status: 'authenticated', session: data.session }
            : { status: 'anonymous' }
        })
      })
      .catch(() => {
        if (!active) return
        setState((prev) => (prev.status === 'loading' ? { status: 'anonymous' } : prev))
      })

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, session) => {
      if (!active) return
      setState(session ? { status: 'authenticated', session } : { status: 'anonymous' })
    })

    return () => {
      active = false
      subscription.unsubscribe()
    }
  }, [])

  return state
}

function CenteredSpin() {
  return (
    <Flex justify="center" align="center" style={{ minHeight: '100vh' }}>
      <Spin size="large" aria-label="Checking your session" />
    </Flex>
  )
}

/** Guards nested routes behind an authenticated Supabase session. */
function AuthGuard() {
  const state = useSessionState()

  if (state.status === 'loading') return <CenteredSpin />
  if (state.status === 'anonymous') return <Navigate to="/login" replace />

  return <AppFrame email={state.session.user.email ?? ''} />
}

/** Public login route; already-authenticated users are sent to the app. */
function LoginRoute() {
  const state = useSessionState()

  if (state.status === 'loading') return <CenteredSpin />
  if (state.status === 'authenticated') return <Navigate to="/" replace />

  return <LoginPage />
}

const NAV_LINKS = [
  { to: '/', label: 'Inventory', end: true },
  { to: '/mission-control', label: 'Mission Control', end: false },
]

function AppFrame({ email }: { email: string }) {
  const { token } = theme.useToken()

  function handleLogout() {
    void supabase.auth.signOut()
  }

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header>
        <Flex align="center" justify="space-between" style={{ height: '100%' }}>
          <Flex align="center" gap={32}>
            <Title level={3} style={{ color: token.colorTextLightSolid, margin: 0 }}>
              Nextlane DMS
            </Title>
            <Flex align="center" gap={4} component="nav">
              {NAV_LINKS.map((link) => (
                <NavLink
                  key={link.to}
                  to={link.to}
                  end={link.end}
                  style={({ isActive }) => ({
                    color: token.colorTextLightSolid,
                    opacity: isActive ? 1 : 0.7,
                    fontWeight: isActive ? 600 : 400,
                    padding: '6px 12px',
                    borderRadius: token.borderRadius,
                    background: isActive ? 'rgba(255, 255, 255, 0.16)' : 'transparent',
                  })}
                >
                  {link.label}
                </NavLink>
              ))}
            </Flex>
          </Flex>
          <Flex align="center" gap={16}>
            <ReportIssueModal />
            <Text style={{ color: token.colorTextLightSolid, opacity: 0.85 }}>{email}</Text>
            <Button onClick={handleLogout}>Log out</Button>
          </Flex>
        </Flex>
      </Header>
      <Content style={{ padding: 24 }}>
        <Outlet />
      </Content>
    </Layout>
  )
}

function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginRoute />} />
      <Route path="/" element={<AuthGuard />}>
        <Route index element={<InventoryPage />} />
        <Route path="mission-control" element={<MissionControlPage />} />
      </Route>
    </Routes>
  )
}

export default App
