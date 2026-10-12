"use client"

import { useEffect, useState, type FormEvent, type ReactNode } from "react"
import { QueryState } from "@/components/query-state"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { ConflictPanel, type ConflictRow } from "@/features/config/conflict-panel"
import { FormProvider, UNSET_HINT } from "@/features/config/fields"
import { ConfigHistory } from "@/features/config/history"
import { useSaveConfig } from "@/features/config/mutations"
import { fetchConfig, useConfig, useConfigChanges } from "@/features/config/queries"
import { ContextSection } from "@/features/config/sections/context"
import { EntryValiditySection } from "@/features/config/sections/entry-validity"
import { ExitSection } from "@/features/config/sections/exit"
import { ExpirySection } from "@/features/config/sections/expiry"
import { FeedSection } from "@/features/config/sections/feed"
import { ModeSection } from "@/features/config/sections/mode"
import { ModelsSection } from "@/features/config/sections/models"
import { RiskSection } from "@/features/config/sections/risk"
import { RoutingSection } from "@/features/config/sections/routing"
import { ValidationSection } from "@/features/config/sections/validation"
import {
  NO_ERRORS,
  canon,
  changedKeys,
  llmError,
  normalize,
  parseServerErrors,
  toDoc,
  toDraft,
  type ConfigResponse,
  type Draft,
  type ServerErrors,
} from "@/features/config/types"
import { validateDraft } from "@/features/config/validate"
import { formatTime } from "@/lib/format"


function MissingSummary({ keys }: { keys: string[] }) {
  if (keys.length === 0) return null
  return (
    <div role="status" className="rounded-lg border border-warning bg-warning/5 p-3 text-sm">
      <p className="font-medium">Missing for entries: new entry cards stay disabled until these are set.</p>
      <ul aria-label="Missing for entries" className="mt-2 grid gap-x-4 gap-y-1 sm:grid-cols-2">
        {keys.map((key) => (
          <li key={key}>
            <code className="text-xs">{key}</code> <span className="text-muted-foreground">({UNSET_HINT})</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** Changes whose effect on trading is large enough to read before saving. */
const HIGH_IMPACT = [
  "trading_mode",
  "max_total_exposure_usd",
  "max_daily_loss_usd",
  "max_open_positions",
  "entry_chase_pct",
]

const CATEGORIES = [
  { id: "mode", title: "Mode & Execution" },
  { id: "feed", title: "Feed & Discord" },
  { id: "risk", title: "Risk" },
  { id: "exit", title: "Exit & Expiry" },
  { id: "validation", title: "Validation" },
  { id: "ai", title: "AI & Processing" },
  { id: "history", title: "History" },
] as const

const show = (value: unknown) => JSON.stringify(value) ?? "unset"

function Category({ id, children }: { id: (typeof CATEGORIES)[number]["id"]; children: ReactNode }) {
  const title = CATEGORIES.find((category) => category.id === id)!.title
  return (
    <section id={id} aria-labelledby={`${id}-title`} className="flex scroll-mt-4 flex-col gap-3">
      <h2 id={`${id}-title`} className="text-lg font-medium">
        {title}
      </h2>
      {children}
    </section>
  )
}

function ConfigNav() {
  return (
    <nav aria-label="Config sections" className="lg:sticky lg:top-4 lg:self-start">
      <ul className="flex flex-wrap gap-2 lg:flex-col lg:gap-1">
        {CATEGORIES.map((category) => (
          <li key={category.id}>
            <a
              href={`#${category.id}`}
              className="inline-flex min-h-9 items-center rounded-md border border-border px-3 text-sm hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none lg:w-full lg:border-transparent"
            >
              {category.title}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  )
}

function ConfigHeader({ version, never, dirtyCount }: { version: number; never: boolean; dirtyCount: number }) {
  const changes = useConfigChanges()
  const last = changes.data?.items.find((change) => change.version === version)
  return (
    <header className="flex flex-col gap-1">
      <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
        <span>
          Version {version}
          {never ? " (never saved: showing defaults)" : ""}
        </span>
        {last ? (
          <span>
            Last updated <time dateTime={last.at}>{formatTime(last.at)}</time> by {last.actor}
          </span>
        ) : null}
        {dirtyCount > 0 ? (
          <span role="status" className="rounded bg-warning/15 px-1.5 py-0.5 font-medium text-foreground">
            Unsaved changes
          </span>
        ) : null}
      </p>
      <p className="text-xs text-muted-foreground">
        Saved changes apply to the running system immediately and are recorded in History. Unsaved edits change nothing.
      </p>
    </header>
  )
}

function Editor({ loaded }: { loaded: ConfigResponse }) {
  // `base` is the version last loaded or saved: it is what a save is checked against.
  const [base, setBase] = useState(loaded)
  const [draft, setDraft] = useState<Draft>(() => toDraft(loaded.document))
  const [reason, setReason] = useState("")
  const [errors, setErrors] = useState<ServerErrors>(NO_ERRORS)
  const [conflict, setConflict] = useState<ConfigResponse | null>(null)
  const [confirming, setConfirming] = useState(false)
  const save = useSaveConfig()

  const mine = toDoc(draft)
  const baseDoc = normalize(base.document)
  const edited = changedKeys(baseDoc, mine)
  const jsonError = llmError(draft.llm)
  const problems = validateDraft(draft)
  if (jsonError) problems.llm = jsonError
  const problemCount = Object.keys(problems).length
  const dirty = edited.length > 0 || base.version === 0
  const canSave = reason.trim() !== "" && problemCount === 0 && !conflict && !save.isPending && dirty
  const highImpact = edited.filter((key) => HIGH_IMPACT.includes(key))

  // Leaving the page (close, reload, external link) with unsaved edits asks first.
  useEffect(() => {
    if (edited.length === 0) return
    const warn = (event: BeforeUnloadEvent) => event.preventDefault()
    window.addEventListener("beforeunload", warn)
    return () => window.removeEventListener("beforeunload", warn)
  }, [edited.length])

  const adopt = (config: ConfigResponse, document = config.document) => {
    setBase(config)
    setDraft(toDraft(document))
    setConflict(null)
    setErrors(NO_ERRORS)
  }

  function send() {
    setConfirming(false)
    setErrors(NO_ERRORS)
    save.mutate(
      { expectedVersion: base.version, document: mine, reason: reason.trim() },
      {
        onSuccess: (saved) => {
          adopt(saved)
          setReason("")
        },
        onError: (error) => {
          if (error.status === 409) {
            fetchConfig().then(setConflict, (failure: Error) => setErrors({ fields: {}, general: [failure.message] }))
          } else if (error.status === 422) {
            setErrors(parseServerErrors(error.message))
          } else {
            setErrors({ fields: {}, general: [error.message] })
          }
        },
      },
    )
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    if (!canSave) return
    if (highImpact.length > 0) setConfirming(true)
    else send()
  }

  const conflictRows: ConflictRow[] = conflict
    ? edited
        .filter((key) => canon(mine[key]) !== canon(normalize(conflict.document)[key]))
        .map((key) => ({ key, mine: mine[key], server: normalize(conflict.document)[key] }))
    : []
  // Reapply: the server's document with only the fields this session edited put back on top.
  const reapply = () =>
    conflict && adopt(conflict, { ...conflict.document, ...Object.fromEntries(edited.map((key) => [key, mine[key]])) })

  const missing = new Set(base.missingForEntries)

  return (
    <form onSubmit={submit} className="flex flex-col gap-4 pb-32">
      <ConfigHeader version={base.version} never={base.version === 0} dirtyCount={edited.length} />
      <MissingSummary keys={base.missingForEntries} />
      {conflict ? (
        <ConflictPanel version={conflict.version} rows={conflictRows} onReload={() => adopt(conflict)} onReapply={reapply} />
      ) : null}
      <FormProvider
        value={{ draft, patch: (changes) => setDraft((current) => ({ ...current, ...changes })), missing, errors, problems }}
      >
        <Category id="mode">
          <ModeSection />
          <EntryValiditySection />
        </Category>
        <Category id="feed">
          <FeedSection />
        </Category>
        <Category id="risk">
          <RiskSection />
        </Category>
        <Category id="exit">
          <ExitSection />
          <ExpirySection />
        </Category>
        <Category id="validation">
          <ValidationSection />
        </Category>
        <Category id="ai">
          <ContextSection />
          <RoutingSection />
          <ModelsSection />
        </Category>
      </FormProvider>
      {dirty ? (
        <div className="fixed inset-x-0 bottom-0 z-40 border-t border-border bg-background/95 px-4 py-2 shadow-[0_-2px_8px_rgb(0_0_0/0.08)] backdrop-blur sm:px-6">
          <div className="mx-auto flex max-w-7xl flex-col gap-2">
            {errors.general.map((message) => (
              <p key={message} role="alert" className="text-sm text-destructive">
                {message}
              </p>
            ))}
            <div className="flex flex-wrap items-end gap-x-3 gap-y-2">
              <div className="flex min-w-48 flex-1 flex-col gap-1">
                <Label htmlFor="config-reason" className="text-xs leading-normal select-text">
                  Reason for this change
                </Label>
                <Input
                  id="config-reason"
                  value={reason}
                  placeholder="e.g. Raised exposure cap after review"
                  onChange={(event) => setReason(event.target.value)}
                />
              </div>
              <p aria-live="polite" className="text-sm text-muted-foreground">
                {edited.length > 0 ? `${edited.length} unsaved ${edited.length === 1 ? "change" : "changes"}` : "Initial save"}
                {problemCount > 0 ? ` · ${problemCount} to fix` : ""}
                {problemCount === 0 && reason.trim() === "" ? " · reason required" : ""}
              </p>
              <Button type="button" variant="outline" className="h-11" disabled={save.isPending || edited.length === 0} onClick={() => adopt(base)}>
                Discard changes
              </Button>
              <Button type="submit" className="h-11" disabled={!canSave}>
                {save.isPending ? "Saving…" : "Save config"}
              </Button>
            </div>
          </div>
        </div>
      ) : null}
      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Confirm trading-impact changes</AlertDialogTitle>
            <AlertDialogDescription>
              These apply to the running system as soon as the save succeeds.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <ul aria-label="High-impact changes" className="flex max-h-60 flex-col gap-2 overflow-y-auto text-xs">
            {highImpact.map((key) => (
              <li key={key} className="rounded bg-muted/50 p-2">
                <code className="font-semibold">{key}</code>
                <div className="font-mono break-all">Before: {show(baseDoc[key])}</div>
                <div className="font-mono break-all">After: {show(mine[key])}</div>
              </li>
            ))}
          </ul>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep editing</AlertDialogCancel>
            <AlertDialogAction onClick={send}>Confirm and save</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </form>
  )
}

export function ConfigScreen() {
  const query = useConfig()
  return (
    <div className="grid gap-6 lg:grid-cols-[13rem_minmax(0,1fr)]">
      <ConfigNav />
      <div className="flex min-w-0 flex-col gap-8">
        <QueryState query={query} loadingLabel="Loading config" emptyTitle="No config">
          {(config) => <Editor loaded={config} />}
        </QueryState>
        <ConfigHistory />
      </div>
    </div>
  )
}
