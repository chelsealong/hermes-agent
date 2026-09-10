/**
 * Runtime SDK injection — the other half of the vscode-module model. Bundled
 * plugins resolve `@hermes/plugin-sdk` through the vite alias; RUNTIME-loaded
 * plugins (disk / fetched) import the same specifier and get the same object:
 * the loader rewrites bare specifiers to shim modules that re-export the
 * live namespaces installed here. React ships as the app's singletons —
 * a second React instance would break hooks.
 */

import * as React from 'react'
import * as jsxDevRuntime from 'react/jsx-dev-runtime'
import * as jsxRuntime from 'react/jsx-runtime'

import * as sdk from './index'

// A function, not a module-level const: `./index` re-exports `SkillsView`
// from `@/app/skills`, whose tree imports `@/contrib/runtime-loader`, which
// imports this file — a real import cycle back to `./index`. The production
// bundler places this module's code ahead of `./index`'s in their shared
// chunk, so a top-level object literal here would capture `sdk` before
// index.ts's own module body has run (#107291 — every runtime plugin failed
// with "Cannot convert undefined or null to object" at eval of the sdk
// chunk). Deferring the snapshot to call time reads the live binding once
// every module in the cycle has finished initializing — both call sites here
// only ever run well after boot, when a plugin actually loads.
function globals() {
  return {
    __HERMES_PLUGIN_SDK__: sdk,
    __HERMES_REACT__: React,
    __HERMES_REACT_JSX__: jsxRuntime,
    __HERMES_REACT_JSX_DEV__: jsxDevRuntime
  } as const
}

export function installPluginSdk(): void {
  Object.assign(globalThis, globals())
}

/** Build a shim ESM blob that re-exports a global namespace's live members.
 *  Export names come from the namespace itself, so the list can't drift. */
function shimUrl(globalKey: keyof ReturnType<typeof globals>): string {
  const names = Object.keys(globals()[globalKey]).filter(
    name => name !== 'default' && /^[A-Za-z_$][\w$]*$/.test(name)
  )

  const source =
    `const m = globalThis.${globalKey};\n` +
    `export default m.default ?? m;\n` +
    // Guard the destructuring: `export const {  } = m` is a syntax error, so
    // only emit it when the namespace actually has named exports.
    (names.length ? `export const { ${names.join(', ')} } = m;\n` : '')

  return URL.createObjectURL(new Blob([source], { type: 'text/javascript' }))
}

let cached: Record<string, string> | null = null

/** Specifier -> shim URL map for the runtime loader (longest keys first). */
export function sdkImportMap(): Record<string, string> {
  cached ??= {
    '@hermes/plugin-sdk': shimUrl('__HERMES_PLUGIN_SDK__'),
    'react/jsx-dev-runtime': shimUrl('__HERMES_REACT_JSX_DEV__'),
    'react/jsx-runtime': shimUrl('__HERMES_REACT_JSX__'),
    react: shimUrl('__HERMES_REACT__')
  }

  return cached
}
