import { useState } from 'react'
import { App, Button, Form, Input, Modal } from 'antd'
import { useLocation } from 'react-router-dom'
import { ApiError, api } from '../lib/api'

const { TextArea } = Input

// keep in sync with api/_lib/events.py's MAX_MESSAGE_CHARS
const MESSAGE_MAX_CHARS = 4000

interface ReportIssueFormValues {
  message: string
}

/** Header trigger + modal: reports a user-submitted issue as a `bug_report` app event. */
function ReportIssueModal() {
  const { message: toast } = App.useApp()
  const location = useLocation()
  const [open, setOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [form] = Form.useForm<ReportIssueFormValues>()

  function openModal() {
    setOpen(true)
  }

  function closeModal() {
    setOpen(false)
    form.resetFields()
  }

  async function handleFinish(values: ReportIssueFormValues) {
    setSubmitting(true)
    try {
      await api.post('/api/events', {
        kind: 'bug_report',
        message: values.message,
        context: { page: location.pathname },
      })
      toast.success('Thanks — your report was sent.')
      closeModal()
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Failed to send report.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <>
      <Button onClick={openModal}>Report issue</Button>
      <Modal
        title="Report an issue"
        open={open}
        onCancel={closeModal}
        onOk={() => form.submit()}
        confirmLoading={submitting}
        okText="Send report"
        destroyOnHidden
      >
        <Form<ReportIssueFormValues>
          form={form}
          layout="vertical"
          disabled={submitting}
          onFinish={(values) => void handleFinish(values)}
        >
          <Form.Item
            label="What went wrong?"
            name="message"
            rules={[
              { required: true, message: 'Please describe the issue' },
              { max: MESSAGE_MAX_CHARS, message: `Keep it under ${MESSAGE_MAX_CHARS} characters` },
            ]}
          >
            <TextArea
              rows={5}
              maxLength={MESSAGE_MAX_CHARS}
              showCount
              placeholder="Describe what happened, and what you expected instead..."
            />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}

export default ReportIssueModal
