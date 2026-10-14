import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register, RenderElement } from 'claude-code'
import {
  codePaths, covers, decision, describe, frontmatter, HUB, isAccepted, isRecord, listOf, mdLinks, normalize, parseLog,
  paths, reminder, SHA, stateProblems, supersedes,
} from './docs.ts'
import type { Front } from './docs.ts'
import type { DocsReport } from './types.d.ts'
import { GUIDANCE, START } from './guidance.ts'

// The ask of docs/adr/0001, lever 1: the UserPromptSubmit hook removed in
// 0573f2e, back in-process and only where `silica-core init` left vault.yaml.
// QUESTION and IDENTIFIER are its two regexes as measured on 2026-09-10.
const QUESTION = /\?|^\s*(what|where|how|why|which|when|who|does|do|is|are|can|list|explain|describe|find)\b/i
// a backtick, snake_case, camelCase, a path or an extension, `::`, `#L`: the
// prompt names something grep can look for
const IDENTIFIER = /`|\b[A-Za-z]\w*_\w+\b|\b[A-Za-z]*[a-z][A-Z]\w*\b|\/\w|\.\w{1,4}\b|::|#L\d/

export const NUDGE = 'silica-core: this question names no identifier. Before any grep (a Grep tool, or grep or rg in a shell), Glob or Read, state whether silica_search applies (yes or no); if yes, call it first with the question as `query`.'

// ponytail: the folder the session started in; SILICA_VAULT pointing elsewhere is not seen (ADR-0001)
async function adopted($: EngineInterface): Promise<boolean> {
  const root = await $.session.root()
  return $.fs.exists(`${root}/vault.yaml`)
}

// Where `silica-core setup claude` writes its block: guidance.claude_md_path().
async function claudeMdHoldsTheBlock($: EngineInterface): Promise<boolean> {
  const dir = (await $.env.get('CLAUDE_CONFIG_DIR'))
    || `${(await $.env.get('HOME')) ?? (await $.env.get('USERPROFILE'))}/.claude`
  const text = await $.fs.read(`${dir}/CLAUDE.md`).catch(() => '')
  return String(text).includes(START)
}

// Lever 5: the folder that holds hooks/ is the plugin root (the module does not
// see CLAUDE_PLUGIN_ROOT), and init runs through mcp.json's own launch, extras
// and --frozen included, so uv never re-syncs the environment the server shares
const PLUGIN_ROOT = decodeURIComponent(new URL('..', (import.meta as { url: string }).url).pathname).replace(/\/$/, '').replace(/^\/([A-Za-z]:)/, '$1')
const INIT = ['uv', 'run', '--frozen', '--project', PLUGIN_ROOT, '--extra', 'mcp', '--extra', 'dense', 'silica-core', 'init']
const offer = atom({ plugin: 'silica-core', key: 'offer' } as const, 'hidden')

// The docs upkeep of docs/adr/0004
const docs = atom({ plugin: 'silica-core', key: 'docs' } as const, null)
const isDocsHidden = atom({ plugin: 'silica-core', key: 'isDocsHidden' } as const, false)
// ponytail: 30 changed lines is a guess at where a small turn ends; move it after some use
const THRESHOLD = 30
const TEST = /(^|\/)(tests?|__tests__)\/|\.test\.|_test\.|(^|\/)test_[^/]*$/
const MANIFEST = /(^|\/)(pyproject\.toml|package\.json|Cargo\.toml|go\.mod|plugin\.json|hooks\.json|Dockerfile)$/
const WRITES = new Set(['Edit', 'Write', 'NotebookEdit'])

// ponytail: one turn of the main loop at a time, in module state; a reload
// forgets the turn, and writes made by subagents or through Bash are not seen.
// `docs` holds each doc the turn wrote, with its text before the first write.
const fresh = () => ({ lines: 0, files: new Set<string>(), isDone: false, docs: new Map<string, string | null>(),
                       records: undefined as Map<string, string[]> | undefined })
let turn = fresh()
// the lint's blocks since the turn first tried to end: a fix that leaves new
// problems gets a second, the same problems again end the turn. Kept apart
// from `turn` in case the continuation a block causes raises turn.start.
let stops = { blocks: 0, last: '' }

// the + and - lines of a Write's or an Edit's structuredPatch
const changed = (result: any): number =>
  (result?.structuredPatch ?? []).reduce((n: number, h: any) =>
    n + (h.lines ?? []).filter((l: string) => l.startsWith('+') || l.startsWith('-')).length, 0)

// The docs upkeep's reading through the engine, git and the files
// (docs/adr/0004); what reads text alone is in docs.ts

async function git($: EngineInterface, root: string, args: string[]): Promise<string> {
  const r = await $.process.run(['git', '-C', root, ...args], { timeoutMs: 30_000 }).catch(() => undefined)
  return r && r.exitCode === 0 ? r.stdout : ''
}

// every commit reachable from HEAD, or null where that cannot be known (not a repo, a shallow clone)
async function commits($: EngineInterface, root: string): Promise<string[] | null> {
  const shallow = await git($, root, ['rev-parse', '--is-shallow-repository'])
  if (shallow.trim() !== 'false') return null
  const all = await git($, root, ['rev-list', 'HEAD'])
  return all.split('\n').filter(Boolean)
}

// a file's text, undefined when it cannot be read
async function readText($: EngineInterface, path: string): Promise<string | undefined> {
  const t = await $.fs.read(path).catch(() => undefined)
  return t === undefined ? undefined : String(t)
}

// whether a root-relative path, a folder with its trailing slash or without, is in the tree
async function isThere($: EngineInterface, root: string, rel: string): Promise<boolean> {
  return $.fs.exists(`${root}/${rel.replace(/\/$/, '')}`)
}

// the records under docs/adr/ and docs/specs/, root-relative
async function records($: EngineInterface, root: string): Promise<string[]> {
  const adr = await $.fs.list(`${root}/docs/adr`).catch(() => [])
  const specs = await $.fs.list(`${root}/docs/specs`).catch(() => [])
  const out = adr.map(x => `docs/adr/${x.name}`)
  for (const x of specs) out.push(x.kind === 'dir' ? `docs/specs/${x.name}/spec.md` : `docs/specs/${x.name}`)
  return out.filter(isRecord)
}

// what a record names that is not there: a SHA that is not one commit
// reachable from HEAD, a path of implemented_in or documents not in the tree
async function evidence($: EngineInterface, root: string, fm: Front, known: string[] | null): Promise<string[]> {
  const out: string[] = []
  for (const ref of listOf(fm.implemented_in)) {
    if (SHA.test(ref)) {
      if (known && known.filter(c => c.startsWith(ref)).length !== 1) out.push(`${ref} is not one commit reachable from HEAD`)
    } else if (!(await isThere($, root, ref))) out.push(`${ref} is neither a commit nor a path in the tree`)
  }
  for (const ref of listOf(fm.documents)) {
    if (!SHA.test(ref) && !(await isThere($, root, ref))) out.push(`documents names ${ref}, which is not in the tree`)
  }
  return out
}

// each record by its path, with the paths its implemented_in and documents name
async function listed($: EngineInterface, root: string): Promise<Map<string, string[]>> {
  const out = new Map<string, string[]>()
  for (const rec of await records($, root)) {
    const names = paths(frontmatter((await readText($, `${root}/${rec}`)) ?? ''))
    if (names.length) out.set(rec, names)
  }
  return out
}

async function scan($: EngineInterface, root: string): Promise<DocsReport> {
  const recs = await records($, root)
  if (!recs.length) return { problems: [], drift: [] }
  const fms = new Map<string, Front>()
  for (const rec of recs) fms.set(rec, frontmatter((await readText($, `${root}/${rec}`)) ?? ''))

  const known = await commits($, root)
  const problems: string[] = []
  for (const [rec, fm] of fms) {
    if (fm.implementation === undefined) continue
    for (const p of [...stateProblems(fm), ...(await evidence($, root, fm, known))]) problems.push(`${rec}: ${p}`)
  }

  // drift: commits to a record's files after the record's last commit (its mtime when git does not track it)
  const hubs = new Map<string, number>()
  for (const fm of fms.values()) for (const p of paths(fm)) hubs.set(p, (hubs.get(p) ?? 0) + 1)
  const last = new Map<string, number>()
  for (const c of parseLog(await git($, root, ['log', '--format=@%ct', '--name-only', '--', 'docs/adr', 'docs/specs'])))
    for (const f of c.files) if (!last.has(f)) last.set(f, c.t)
  const anchors = new Map<string, number>()
  for (const rec of recs) {
    const at = last.get(rec)
    if (at !== undefined) { anchors.set(rec, at); continue }
    const st = await $.fs.stat(`${root}/${rec}`).catch(() => undefined)
    anchors.set(rec, Math.floor((st?.mtimeMs ?? 0) / 1000))
  }
  const since = Math.min(...anchors.values())
  const log = parseLog(await git($, root, ['log', `--since=@${since}`, '--format=@%ct', '--name-only']))
  const drift: DocsReport['drift'] = []
  for (const [rec, fm] of fms) {
    if (fm.implementation !== 'implemented' && fm.implementation !== 'partial') continue
    const own = paths(fm).filter(p => (hubs.get(p) ?? 0) < HUB)
    const anchor = anchors.get(rec)!
    const n = own.length ? log.filter(c => c.t > anchor && c.files.some(f => own.some(p => covers(p, f)))).length : 0
    if (n) drift.push({ rec, commits: n })
  }
  drift.sort((a, b) => b.commits - a.commits)
  return { problems, drift }
}

// what the docs this turn wrote get wrong, one line each; `docs` holds each
// with its text before the turn's first write (null for a new one)
async function lint($: EngineInterface, root: string, docs: Map<string, string | null>): Promise<string[]> {
  const out: string[] = []
  const entries = await $.fs.list(root).catch(() => [])
  const top = new Set(entries.filter(x => x.kind === 'dir').map(x => x.name))
  const adrEntries = await $.fs.list(`${root}/docs/adr`).catch(() => [])
  const adrs = adrEntries.map(x => x.name)
  let known: string[] | null | undefined
  for (const [rel, before] of docs) {
    const text = await readText($, `${root}/${rel}`)
    if (text === undefined) continue
    const dir = rel.split('/').slice(0, -1).join('/')
    for (const target of mdLinks(text)) {
      const to = normalize(`${dir}/${target}`)
      if (to === undefined || !(await isThere($, root, to))) out.push(`${rel}: the link to ${target} leads nowhere`)
    }
    for (const p of codePaths(text)) {
      if (top.has(p.split('/')[0]!) && !(await isThere($, root, p))) out.push(`${rel}: \`${p}\` is not in the repo`)
    }
    if (isRecord(rel)) {
      const fm = frontmatter(text)
      if (fm.implementation !== undefined) {
        known ??= await commits($, root)
        for (const p of [...stateProblems(fm), ...(await evidence($, root, fm, known))]) out.push(`${rel}: ${p}`)
      }
      for (const n of supersedes(text)) {
        if (!adrs.some(x => x.startsWith(`${n}-`))) out.push(`${rel}: it names ADR-${n}, which docs/adr/ does not hold`)
      }
    }
    const num = rel.match(/^docs\/adr\/(\d{4})-/)?.[1]
    const same = num ? adrs.filter(x => x.startsWith(`${num}-`)) : []
    if (same.length > 1) out.push(`${rel}: ADR number ${num} is taken by ${same.length} files: ${same.join(', ')}`)
    const was = before != null && rel.startsWith('docs/adr/') && isAccepted(before) ? decision(before) : undefined
    if (was !== undefined && was.trim() !== (decision(text) ?? '').trim()) {
      out.push(`${rel}: the Decision of an accepted ADR changed this turn. If the decision was reversed or replaced, ` +
        'put it back, write a new ADR that supersedes this one and mark this one "Superseded by" it; if the change only clarifies it, say so')
    }
  }
  return out
}

async function adopt($: EngineInterface): Promise<{ isIndexed: boolean; text: string }> {
  const root = await $.session.root()
  const r = await $.process.run(INIT, { cwd: root, timeoutMs: 600_000 })
    .catch(err => ({ exitCode: 1, stderr: String(err) }))
  return r.exitCode === 0
    ? { isIndexed: true, text: `${root} is indexed; silica_search serves it from its next call.` }
    : { isIndexed: false, text: `silica-core init failed in ${root}: ${r.stderr.trim().split('\n').at(-1) ?? ''}` }
}

export const register: Register = (on) => {
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'silica-init', description: 'Index this folder for silica-core (runs silica-core init)' })
    await $.command.register({ name: 'silica-docs', description: 'List the docs/ records that break their implementation state or drift from their code' })
    const root = await $.session.root()
    const home = (await $.env.get('HOME')) ?? (await $.env.get('USERPROFILE'))
    const isAdopted = await adopted($)
    const isAsked = e.isInteractive && root !== '/' && root !== home
      && !isAdopted && !(await $.store.get(`dismissed:${root}`))
    await update($, offer, () => (isAsked ? 'offer' : 'hidden'))
    // after the session is up, so a large repo's git log never delays the first prompt
    if (e.isInteractive && isAdopted) $.clock.after(0, () => { void scan($, root).then(r => update($, docs, () => r)).catch(() => {}) })
    return next(e)
  })

  on('command.run', { command: 'silica-init' }, async ($) => {
    const r = await adopt($)
    if (r.isIndexed) await update($, offer, () => 'hidden')
    return { text: r.text }
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const phase = await read($, offer)
    if (phase === 'hidden' || e.props.hasSurvey) return next(e)
    const { Box, Button, Text } = $.ui.resolve(e)
    if (phase === 'indexing') return h(Box, {}, h(Text, { dimColor: true }, 'silica-core: indexing this folder…')) as RenderElement
    return h(Box, {},
      h(Text, {}, 'silica-core: this folder is not indexed. '),
      h(Button, { key: 'index', label: 'Index', variant: 'primary', onPress: async () => {
        await update($, offer, () => 'indexing')
        const r = await adopt($)
        $.ui.toast(r.text)  // the engine names the plugin in front of it
        await update($, offer, () => (r.isIndexed ? 'hidden' : 'offer'))
      } }),
      h(Button, { key: 'dismiss', label: 'Not here', onPress: async () => {
        await $.store.set(`dismissed:${await $.session.root()}`, true)
        await update($, offer, () => 'hidden')
      } })) as RenderElement
  })

  on('command.run', { command: 'silica-docs' }, async ($) => {
    const r = await scan($, await $.session.root())
    await update($, docs, () => r)
    const text = describe(r)
    return r.problems.length || r.drift.length ? { text, context: [text] } : { text }
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const r = await read($, docs)
    if (e.props.hasSurvey || !r || (!r.problems.length && !r.drift.length) || (await read($, isDocsHidden))) return next(e)
    const { Box, Button, Text } = $.ui.resolve(e)
    return h(Box, {},
      h(Text, { dimColor: true }, `docs: ${r.problems.length} problems · ${r.drift.length} drifting — /silica-docs for the list `),
      h(Button, { key: 'hide-docs', label: 'Hide', onPress: () => update($, isDocsHidden, () => true) })) as RenderElement
  })

  on('turn.start', async ($, e, next) => {
    if (!e.agentId) turn = fresh()
    return next(e)
  })

  // A reminder after a write worth recording, once a turn; the write's result stands as it was
  on('tool.call', async ($, e, next) => {
    const ran = await next(e)
    if (!WRITES.has(e.tool) || e.agentId || ran.deny !== undefined || ran.isError) return ran
    const path: string = (e as any).file_path ?? (e as any).notebook_path ?? ''
    const root = await $.session.root()
    if (!path.startsWith(`${root}/`) || !(await adopted($))) return ran
    const rel = path.slice(root.length + 1)
    if (rel.startsWith('docs/')) {
      if (rel.endsWith('.md') && !turn.docs.has(rel)) turn.docs.set(rel, (ran.result as any)?.originalFile ?? null)
      turn.isDone = true
      return ran
    }
    if (turn.isDone || TEST.test(rel) || !(await $.fs.exists(`${root}/docs`))) return ran

    turn.files.add(rel)
    turn.lines += changed(ran.result)
    turn.records ??= await listed($, root)
    const adrs = [...turn.records].filter(([, entries]) => entries.some(x => covers(x, rel))).map(([adr]) => adr)
    const why = adrs.length ? `${rel} is listed by a record`
      : (ran.result as any)?.type === 'create' ? `${rel} is new`
      : MANIFEST.test(rel) ? `${rel} is a manifest`
      : turn.lines >= THRESHOLD ? `${turn.lines} lines changed`
      : undefined
    if (!why) return ran

    turn.isDone = true
    return { ...ran, context: [...(ran.context ?? []), reminder(why, [...turn.files], adrs)] }
  })

  // The lint of the docs the turn wrote. It asks, it never refuses: the model
  // fixes what it agrees is a defect or says why one is not, and at most two
  // blocks a turn come; what is left after them stays
  on('classic.Stop', async ($, e, next) => {
    const r = await next(e)
    if (!e.stop_hook_active) stops = { blocks: 0, last: '' }
    if (stops.blocks >= 2 || !turn.docs.size) return r
    const found = await lint($, await $.session.root(), turn.docs)
    if (!found.length || found.join('\n') === stops.last) return r
    stops = { blocks: stops.blocks + 1, last: found.join('\n') }
    return { ...r, block: `silica-core: the docs this turn wrote have ${found.length} problem${found.length > 1 ? 's' : ''}:\n` +
      found.map(f => `- ${f}`).join('\n') + '\nFix them before you end the turn, or say in one line why one is not a defect.' }
  })

  on('prompt.submit', async ($, e, next) => {
    const text = e.text.trim()
    if (!text || !QUESTION.test(text) || IDENTIFIER.test(text)) return next(e)
    if (!(await adopted($))) return next(e)
    return next({ ...e, context: [...(e.context ?? []), NUDGE] })
  })

  // Lever 3: the block the bench measured through --append-system-prompt-file,
  // last in the system prompt, where the request offers silica_search
  on('prompt.compose', async ($, e, next) => {
    const composed = await next(e)
    if (!e.tools.some(t => t.endsWith('__silica_search'))) return composed
    if (!(await adopted($)) || (await claudeMdHoldsTheBlock($))) return composed
    return { sections: [...composed.sections, { id: 'silica-core:guidance', text: GUIDANCE, scope: 'session' }] }
  })
}
