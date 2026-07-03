import { Component, type ErrorInfo, type ReactNode } from 'react'
import { Button, Result } from 'antd'
import { api } from '../lib/api'

interface ErrorBoundaryProps {
  children: ReactNode
}

interface ErrorBoundaryState {
  hasError: boolean
}

/**
 * Top-level React error boundary (must be a class component -- boundaries aren't
 * expressible with hooks). Reports the crash as a `client_error` app event
 * (best-effort, never blocks the fallback UI) and offers a reload.
 */
class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false }

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true }
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    // Fire-and-forget: the 16KB serialized `context` cap lives server-side
    // (see api/_lib/events.py) -- message is sliced to 4000 chars and stack
    // to 8000 chars here, keeping us safely under that cap.
    void api
      .post('/api/events', {
        kind: 'client_error',
        message: String(error).slice(0, 4000),
        context: { stack: (errorInfo.componentStack ?? '').slice(0, 8000) },
      })
      .catch(() => {})
  }

  handleReload = () => {
    window.location.reload()
  }

  render() {
    if (this.state.hasError) {
      return (
        <Result
          status="500"
          title="Something went wrong"
          subTitle="An unexpected error occurred. Reloading the page usually fixes it."
          extra={
            <Button type="primary" onClick={this.handleReload}>
              Reload
            </Button>
          }
        />
      )
    }

    return this.props.children
  }
}

export default ErrorBoundary
