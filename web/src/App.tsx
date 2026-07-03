import { App as AntApp, ConfigProvider, Layout, Typography } from 'antd'
import { theme } from './theme'

const { Header, Content } = Layout
const { Title } = Typography

function App() {
  return (
    <ConfigProvider theme={theme}>
      <AntApp>
        <Layout style={{ minHeight: '100vh' }}>
          <Header>
            <Title level={3} style={{ color: '#fff', lineHeight: '64px', margin: 0 }}>
              Nextlane DMS
            </Title>
          </Header>
          <Content style={{ padding: 24 }} />
        </Layout>
      </AntApp>
    </ConfigProvider>
  )
}

export default App
