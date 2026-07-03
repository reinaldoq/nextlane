import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Alert, Button, Card, Flex, Form, Input, Typography, theme } from 'antd'
import { supabase } from '../lib/supabase'

const { Title, Text } = Typography

interface LoginFormValues {
  email: string
  password: string
}

function LoginPage() {
  const navigate = useNavigate()
  const { token } = theme.useToken()
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleFinish(values: LoginFormValues) {
    setSubmitting(true)
    setError(null)

    try {
      const { error: signInError } = await supabase.auth.signInWithPassword({
        email: values.email,
        password: values.password,
      })

      if (signInError) {
        setError(signInError.message)
        return
      }

      void navigate('/', { replace: true })
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Flex
      justify="center"
      align="center"
      style={{ minHeight: '100vh', background: token.colorPrimaryBg }}
    >
      <Card style={{ width: 360 }}>
        <Flex vertical align="center" gap={4} style={{ marginBottom: 24 }}>
          <Title level={3} style={{ margin: 0, color: token.colorPrimary }}>
            Nextlane DMS
          </Title>
          <Text type="secondary">Sign in to continue</Text>
        </Flex>

        {error !== null && (
          <Alert
            type="error"
            message={error}
            showIcon
            closable
            onClose={() => {
              setError(null)
            }}
            style={{ marginBottom: 16 }}
          />
        )}

        <Form<LoginFormValues>
          layout="vertical"
          disabled={submitting}
          onFinish={(values) => {
            void handleFinish(values)
          }}
        >
          <Form.Item
            label="Email"
            name="email"
            rules={[
              { required: true, message: 'Email is required' },
              { type: 'email', message: 'Enter a valid email address' },
            ]}
          >
            <Input autoComplete="email" placeholder="you@dealership.com" />
          </Form.Item>

          <Form.Item
            label="Password"
            name="password"
            rules={[{ required: true, message: 'Password is required' }]}
          >
            <Input.Password autoComplete="current-password" placeholder="Password" />
          </Form.Item>

          <Form.Item style={{ marginBottom: 0 }}>
            <Button type="primary" htmlType="submit" block loading={submitting}>
              Sign in
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </Flex>
  )
}

export default LoginPage
