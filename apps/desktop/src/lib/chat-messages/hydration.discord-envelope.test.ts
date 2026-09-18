import { expect, it } from 'vitest'

import { chatMessageText, toChatMessages } from './index'

// The Discord adapter wraps the per-turn user text in a model-facing routing
// envelope and that wrapped text is persisted as the row's display content, so
// the desktop transcript would otherwise render the envelope instead of what
// the user typed (#114719).
const envelope = (id: string) =>
  `[Triggering message id: \`${id}\` — use as \`message_id\` for reply/react/pin via the discord tools.]`

const userText = (messages: Parameters<typeof toChatMessages>[0]): string =>
  chatMessageText(toChatMessages(messages).find(message => message.role === 'user')!)

it('drops the Discord triggering-message envelope from a persisted user turn', () => {
  const authored = 'Create a project plan'

  expect(userText([{ role: 'user', content: `${envelope('1550380365858865156')}\n\n${authored}`, timestamp: 1 }])).toBe(
    authored
  )
})

it('keeps the [Replying to: "…"] quote pointer while dropping the triggering envelope', () => {
  const authored = 'ship it'
  const replyPointer = '[Replying to: "the earlier plan"]'

  // The gateway prepends the reply pointer AFTER the triggering envelope, so it
  // ends up ahead of the envelope in the stored text.
  const stored = `${replyPointer}\n\n${envelope('42')}\n\n${authored}`

  expect(userText([{ role: 'user', content: stored, timestamp: 1 }])).toBe(`${replyPointer}\n\n${authored}`)
})

it('leaves a lone reply pointer untouched (it is authored context, not routing)', () => {
  const stored = '[Replying to: "the earlier plan"]\n\nship it'

  expect(userText([{ role: 'user', content: stored, timestamp: 1 }])).toBe(stored)
})

it('still hoists attached-context refs after the envelope is stripped', () => {
  const stored = `${envelope('7')}\n\nlook here\n--- Attached Context ---\n@file:notes.md`

  expect(userText([{ role: 'user', content: stored, timestamp: 1 }])).toBe('@file:notes.md\n\nlook here')
})
