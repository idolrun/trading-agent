"use client"

import { Textarea } from "@/components/ui/textarea"

import { useState, type FormEvent } from "react"
import { QueryState } from "@/components/query-state"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { ConflictPanel, type ConflictRow } from "@/features/config/conflict-panel"
import { FormProvider, UNSET_HINT } from "@/features/config/fields"
import { ConfigHistory } from "@/features/config/history"
import { useSaveConfig } from "@/features/config/mutations"
import { fetchConfig, useConfig } from "@/features/config/queries"
import { ContextSection } from "@/features/config/sections/context"
import { EntryValiditySection } from "@/features/config/sections/entry-validity"
import { ExitSection } from "@/features/config/sections/exit"
import { ExpirySection } from "@/features/config/sections/expiry"
import { FeedSection } from "@/features/config/sections/feed"
import { ModeSection } from "@/features/config/sections/mode"
import { ModelsSection } from "@/features/config/sections/models"
import { RiskSection } from "@/features/config/sections/risk"
import { RoutingSection } from "@/features/config/sections/routing"
import { SizingSection } from "@/features/config/sections/sizing"
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


function MissingSummary({ keys }: { keys: string[] }) {
  if (keys.length === 0) return null
  return (
    <div role="status" className="rounded-lg border border-warning bg-warning/5 p-4 text-sm">
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

function Editor({ loaded }: { loaded: ConfigResponse }) {
  // `base` is the version last loaded or saved: it is what a save is checked against.
  const [base, setBase] = useState(loaded)
  const [draft, setDraft] = useState<Draft>(() => toDraft(loaded.document))
  const [reason, setReason] = useState("")
  const [errors, setErrors] = useState<ServerErrors>(NO_ERRORS)
  const [conflict, setConflict] = useState<ConfigResponse | null>(null)
  const save = useSaveConfig()

  const mine = toDoc(draft)
  const baseDoc = normalize(base.document)
  const edited = changedKeys(baseDoc, mine)
  const jsonError = llmError(draft.llm)
  const canSave = reason.trim() !== "" && !jsonError && !conflict && !save.isPending && (edited.length > 0 || base.version === 0)

  const adopt = (config: ConfigResponse, document = config.document) => {
    setBase(config)
    setDraft(toDraft(document))
    setConflict(null)
    setErrors(NO_ERRORS)
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    if (!canSave) return
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
    <form onSubmit={submit} className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">Version {base.version}{base.version === 0 ? " (never saved: showing defaults)" : ""}</p>
      <MissingSummary keys={base.missingForEntries} />
      {conflict ? (
        <ConflictPanel version={conflict.version} rows={conflictRows} onReload={() => adopt(conflict)} onReapply={reapply} />
      ) : null}
      <FormProvider value={{ draft, patch: (changes) => setDraft((current) => ({ ...current, ...changes })), missing, errors }}>
        <ModeSection />
        <FeedSection />
        <ContextSection />
        <SizingSection />
        <EntryValiditySection />
        <ExitSection />
        <ExpirySection />
        <RiskSection />
        <ValidationSection />
        <RoutingSection />
        <ModelsSection />
      </FormProvider>
      <div className="sticky bottom-0 flex flex-col gap-2 border-t border-border bg-background py-3">
        {errors.general.map((message) => (
          <p key={message} role="alert" className="text-sm text-destructive">
            {message}
          </p>
        ))}
        <Label htmlFor="config-reason" className="leading-normal select-text">
          Reason for this change
        </Label>
        <Textarea
          id="config-reason"
          rows={1}
         
          value={reason}
          onChange={(event) => setReason(event.target.value)}
        />
        <div className="flex items-center gap-3">
          <Button type="submit" className="h-11" disabled={!canSave}>
            {save.isPending ? "Saving…" : "Save config"}
          </Button>
          {edited.length > 0 ? <span className="text-sm text-muted-foreground">Unsaved changes: {edited.length}</span> : null}
        </div>
      </div>
    </form>
  )
}

export function ConfigScreen() {
  const query = useConfig()
  return (
    <div className="flex flex-col gap-8">
      <QueryState query={query} loadingLabel="Loading config" emptyTitle="No config">
        {(config) => <Editor loaded={config} />}
      </QueryState>
      <ConfigHistory />
    </div>
  )
}
