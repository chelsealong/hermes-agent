import { atom } from 'nanostores'

import { persistBoolean, storedBoolean } from '@/lib/storage'

const STORAGE_KEY = 'hermes.desktop.composer.convertLargePastesToAttachment'

/** Desktop-local composer preference; large pastes convert to a .txt attachment by default (#66622). */
export const $convertLargePastesToAttachment = atom(storedBoolean(STORAGE_KEY, true))

$convertLargePastesToAttachment.subscribe(value => persistBoolean(STORAGE_KEY, value))

export function setConvertLargePastesToAttachment(value: boolean) {
  $convertLargePastesToAttachment.set(value)
}
