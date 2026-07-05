import { Fragment } from 'react'
import type { CSSProperties, ReactNode } from 'react'
import { Typography, theme } from 'antd'

const { Text } = Typography

// Render inline **bold** and `code` within one line. React escapes text nodes,
// so this is XSS-safe even though the source is a (semi-trusted) agent message.
function renderInline(line: string): ReactNode[] {
  const nodes: ReactNode[] = []
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g
  let last = 0
  let key = 0
  let m: RegExpExecArray | null
  while ((m = re.exec(line)) !== null) {
    if (m.index > last) nodes.push(line.slice(last, m.index))
    const tok = m[0]
    if (tok.startsWith('**')) {
      nodes.push(
        <Text key={key++} strong>
          {tok.slice(2, -2)}
        </Text>,
      )
    } else {
      nodes.push(
        <Text key={key++} code>
          {tok.slice(1, -1)}
        </Text>,
      )
    }
    last = m.index + tok.length
  }
  if (last < line.length) nodes.push(line.slice(last))
  return nodes
}

/**
 * Minimal, dependency-free markdown renderer for the agent session-summary
 * text stored in `run_steps.detail` -- headings, **bold**, `inline code`, and
 * fenced code blocks, with paragraph line breaks preserved. NOT a full
 * CommonMark parser: just enough to make the common agent output readable, and
 * tolerant of the 500-char truncation (an unterminated fence still renders as a
 * code block to the end). Plain-text details (e.g. "6/6 green") pass straight
 * through unchanged.
 */
function MarkdownLite({ text }: { text: string }) {
  const { token } = theme.useToken()
  const preStyle: CSSProperties = {
    margin: '6px 0',
    padding: '8px 10px',
    background: token.colorFillTertiary,
    borderRadius: token.borderRadius,
    overflowX: 'auto',
    fontSize: 12,
    whiteSpace: 'pre-wrap',
  }

  const blocks: ReactNode[] = []
  let paraBuf: string[] = []
  let codeBuf: string[] = []
  let inCode = false
  let key = 0

  const flushPara = () => {
    if (paraBuf.length === 0) return
    const lines = paraBuf
    paraBuf = []
    blocks.push(
      <p key={key++} style={{ margin: '0 0 6px' }}>
        {lines.map((l, li) => (
          <Fragment key={li}>
            {li > 0 && <br />}
            {renderInline(l)}
          </Fragment>
        ))}
      </p>,
    )
  }
  const flushCode = () => {
    const body = codeBuf.join('\n')
    codeBuf = []
    blocks.push(
      <pre key={key++} style={preStyle}>
        <code>{body}</code>
      </pre>,
    )
  }

  for (const line of text.split('\n')) {
    if (line.trimStart().startsWith('```')) {
      if (inCode) {
        flushCode()
        inCode = false
      } else {
        flushPara()
        inCode = true
      }
      continue
    }
    if (inCode) {
      codeBuf.push(line)
      continue
    }
    if (line.trim() === '') {
      flushPara()
      continue
    }
    const heading = /^\s*#{1,6}\s+(.*)$/.exec(line)
    if (heading) {
      flushPara()
      blocks.push(
        <p key={key++} style={{ margin: '8px 0 2px', fontWeight: 600 }}>
          {renderInline(heading[1] ?? '')}
        </p>,
      )
      continue
    }
    paraBuf.push(line)
  }
  if (inCode) flushCode()
  flushPara()

  return <div style={{ marginTop: 4 }}>{blocks}</div>
}

export default MarkdownLite
