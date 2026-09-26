// Build-time guard: refuse to hand a half-built renderer to electron-builder.
//
// `npm run pack` / `npm run dist*` are `npm run build && npm run builder`.
// If the `build` step (tsc -b && vite build) fails but packaging proceeds
// anyway — a stale checkout that fails typecheck, an interrupted vite build,
// or npm not short-circuiting `&&` in some shells — electron-builder happily
// packages an app with an empty or missing `dist/`. The result launches but
// blank-pages with `ERR_FILE_NOT_FOUND` for dist/index.html, with no clue why.
//
// This runs at the tail of `build`, after vite build, so any packaging path
// inherits it. It fails loud and early instead of shipping a broken bundle.
// See issues #39484 (renderer blank page) and #41327 / #39472 (dashboard 404).

import { existsSync, readFileSync, statSync, readdirSync } from "fs"
import { spawnSync } from "child_process"
import { join, resolve } from "path"
import { isMain } from "./utils.mjs"

const ROUTER_CONTEXT_ERROR = "may be used only in the context of a"

// @tanstack/react-query carries module-level React context (QueryClientContext).
// The entry's QueryClientProvider and every lazy chunk's useQuery must share ONE
// runtime instance; if a build ever emits a second copy, the provider's context
// is invisible to the other copy and useQuery throws "No QueryClient set" — the
// packaged app error-boundaries on launch (#95560). Same single-instance
// invariant as the react-router check above, same failure class.
const QUERY_CLIENT_CONTEXT_ERROR = "No QueryClient set, use QueryClientProvider to set one"

// Pure check — returns { ok: true } or { ok: false, error: "..." }.
// Kept side-effect-free so it can be unit tested without spawning a process.
export function checkDistBuilt(distDir) {
  if (!existsSync(distDir) || !statSync(distDir).isDirectory()) {
    return { ok: false, error: `no dist directory at ${distDir}` }
  }

  const indexHtml = join(distDir, "index.html")
  if (!existsSync(indexHtml) || !statSync(indexHtml).isFile()) {
    return { ok: false, error: `dist/index.html is missing at ${indexHtml}` }
  }
  if (statSync(indexHtml).size === 0) {
    return { ok: false, error: `dist/index.html is empty at ${indexHtml}` }
  }

  // index.html alone isn't enough — vite emits hashed JS into dist/assets.
  // An index.html with no script bundle still blank-pages.
  const assetsDir = join(distDir, "assets")
  const hasAssets =
    existsSync(assetsDir) &&
    statSync(assetsDir).isDirectory() &&
    readdirSync(assetsDir).some(name => name.endsWith(".js"))
  if (!hasAssets) {
    return { ok: false, error: `dist/assets has no built JS bundle (expected vite output under ${assetsDir})` }
  }

  const routerContextAssets = readdirSync(assetsDir)
    .filter(name => name.endsWith(".js"))
    .filter(name => readFileSync(join(assetsDir, name), "utf8").includes(ROUTER_CONTEXT_ERROR))

  if (routerContextAssets.length > 1) {
    return {
      ok: false,
      error: `react-router context invariant found in multiple JS assets: ${routerContextAssets.join(", ")}`
    }
  }

  const queryClientContextAssets = readdirSync(assetsDir)
    .filter(name => name.endsWith(".js"))
    .filter(name => readFileSync(join(assetsDir, name), "utf8").includes(QUERY_CLIENT_CONTEXT_ERROR))

  if (queryClientContextAssets.length > 1) {
    return {
      ok: false,
      error:
        `@tanstack/react-query context invariant found in multiple JS assets: ` +
        `${queryClientContextAssets.join(", ")} — duplicate react-query runtimes make the ` +
        `QueryClientProvider's context invisible to useQuery in other chunks (` +
        `"No QueryClient set" on launch, #95560)`
    }
  }

  // Parse-validate every emitted chunk as an ES module. Corrupted-silent-fail
  // bundles (a dropped identifier token mid-file) produce invalid syntax that
  // only explodes at module-evaluation time in Electron's renderer.
  const chunkParse = verifyChunksParse(assetsDir)
  if (!chunkParse.ok) {
    return chunkParse
  }

  return { ok: true }
}

// Renderer chunks are emitted as ESM (`<script type="module">` in index.html).
// A silent bundler failure can emit syntactically invalid chunks that parse fine
// as CJS-ish text but throw on module evaluation in Electron — the app then
// white-screens with `Uncaught SyntaxError` in the renderer console (observed
// 2026-09: the update-produced bundle was missing a 10-byte identifier token,
// `{$:n,}` vs `{categories:n,}`, leaving an invalid destructuring pattern).
// Parse each emitted chunk as an ES module before packaging so a corrupted
// build fails loudly and the update retry rebuilds instead of shipping it.
//
// All chunks are parsed in ONE child process. A renderer build can emit close
// to a thousand chunks, and spawning a `node --check` per chunk scales with
// process-spawn cost: on a loaded Windows host (real-time AV, ~5s/spawn) that
// ran ~80 minutes past the desktop update hand-off's 600s idle watchdog,
// silently, twice (#123216). `vm.SourceTextModule` parses source as an ES
// module without linking or evaluating it — the same verdict as
// `node --input-type=module --check` — so the whole batch runs in one process.
const PARSE_ALL_CHUNKS_SCRIPT = `
const vm = require("node:vm")
const fs = require("node:fs")
const path = require("node:path")
const dir = process.argv[1]
for (const name of fs.readdirSync(dir).filter(n => n.endsWith(".js"))) {
  try {
    new vm.SourceTextModule(fs.readFileSync(path.join(dir, name), "utf8"))
  } catch (e) {
    process.stdout.write(JSON.stringify({ name, detail: String((e && e.message) || e) }))
    process.exit(3)
  }
}
`

function verifyChunksParse(assetsDir) {
  const nodeBin = process.env.NODE ||
    (process.execPath && /node(\.exe)?$/i.test(process.execPath) ? process.execPath : "node")
  const probe = spawnSync(
    nodeBin,
    ["--experimental-vm-modules", "--no-warnings", "-e", PARSE_ALL_CHUNKS_SCRIPT, assetsDir],
    { maxBuffer: 64 * 1024 * 1024, timeout: 600_000 },
  )
  if (probe.error) {
    return {
      ok: false,
      error: `could not run node to syntax-check renderer chunks: ${probe.error.message}`,
    }
  }
  if (probe.status === 3) {
    let failure = { name: "?", detail: String(probe.stdout || "") }
    try {
      failure = JSON.parse(String(probe.stdout))
    } catch {
      // stdout wasn't the JSON payload we expect; fall back to the raw text above.
    }
    return {
      ok: false,
      error: `built chunk is not valid ES module syntax: ${failure.name} — ${failure.detail}. ` +
        `A renderer chunk failed to parse, so packaging would ship an app that ` +
        `white-screens with "Uncaught SyntaxError" on launch. Re-run the build.`,
    }
  }
  if (probe.status !== 0) {
    const detail = String(probe.stderr || "").trim().split("\n").slice(0, 4).join(" / ")
    return { ok: false, error: `could not syntax-check renderer chunks: ${detail}` }
  }
  return { ok: true }
}

function main() {
  const desktopRoot = resolve(import.meta.dirname, "..")
  const distDir = join(desktopRoot, "dist")
  const result = checkDistBuilt(distDir)

  if (!result.ok) {
    console.error(`\n✗ assert-dist-built: ${result.error}`)
    console.error("  The renderer bundle is missing or incomplete, so packaging")
    console.error("  would produce an app that launches to a blank page.")
    console.error("  Re-run the build and check the tsc/vite output above for the")
    console.error("  real failure, then package again:")
    console.error(`    cd ${desktopRoot} && npm run build\n`)
    process.exit(1)
  }

  console.log("✓ assert-dist-built: dist/index.html + assets present")
}

if (isMain(import.meta.url)) {
  main()
}

export default { checkDistBuilt }
