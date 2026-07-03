import { useEffect, useState } from 'react'
import { Navigate, Outlet, Route, Routes } from 'react-router-dom'
import { Button, Flex, Layout, Spin, Typography, theme } from 'antd'
import type { Session } from '@supabase/supabase-js'
import { supabase } from './lib/supabase'
import LoginPage from './pages/LoginPage'

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

function AppFrame({ email }: { email: string }) {
  const { token } = theme.useToken()

  function handleLogout() {
    void supabase.auth.signOut()
  }

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header>
        <Flex align="center" justify="space-between" style={{ height: '100%' }}>
          <Title level={3} style={{ color: token.colorTextLightSolid, margin: 0 }}>
            Nextlane DMS
          </Title>
          <Flex align="center" gap={16}>
            {/* Task 12: "Report issue" trigger goes here */}
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

function InventoryPlaceholder() {
  return <Text>Inventory coming soon</Text>
}

function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginRoute />} />
      <Route path="/" element={<AuthGuard />}>
        <Route index element={<InventoryPlaceholder />} />
      </Route>
    </Routes>
  )
}

export default App
