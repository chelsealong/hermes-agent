import { atom } from 'nanostores'

// `display.code_blocks.expand_ascii_diagrams` — Settings-only toggle (no
// composer quick-action), so this atom is seed-only: refreshed from config on
// load/profile-switch and read by the syntax highlighter to decide whether a
// `txt`/`console` fence should mount already expanded. Off by default,
// matching the backend default.
export const $expandAsciiDiagrams = atom<boolean>(false)

/** Seed the atom from a loaded config payload (mount / refresh). */
export function applyExpandAsciiDiagramsFromConfig(
  config: { display?: { code_blocks?: { expand_ascii_diagrams?: unknown } } | null } | null | undefined
) {
  $expandAsciiDiagrams.set(Boolean(config?.display?.code_blocks?.expand_ascii_diagrams))
}
