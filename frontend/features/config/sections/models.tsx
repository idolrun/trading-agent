"use client"

import { CheckField, ConfigField, FieldErrors, ScalarField, Section, useConfigForm } from "@/features/config/fields"
import { llmError } from "@/features/config/types"

export function ModelsSection() {
  const { draft, patch } = useConfigForm()
  const { jev } = draft
  const setJev = (change: Partial<typeof jev>) => patch({ jev: { ...jev, ...change } })
  const { laya } = draft
  const setLaya = (change: Partial<typeof laya>) => patch({ laya: { ...laya, ...change } })
  const jsonError = llmError(draft.llm)
  return (
    <Section title="Models">
      <div className="flex flex-col gap-2 sm:col-span-2">
        <h3 className="text-sm font-medium">Jev</h3>
        <div className="grid gap-3 sm:grid-cols-2">
          <ConfigField name="jev_model" label="Jev model">
            {(control) => <input {...control} value={jev.model} onChange={(event) => setJev({ model: event.target.value })} />}
          </ConfigField>
          <ConfigField name="jev_timeout_ms" label="Jev timeout (ms)">
            {(control) => (
              <input {...control} type="number" step={1} value={jev.timeout_ms} onChange={(event) => setJev({ timeout_ms: event.target.value })} />
            )}
          </ConfigField>
        </div>
        <CheckField label="Jev enabled" checked={jev.enabled} onChange={(enabled) => setJev({ enabled })} />
        <FieldErrors name="jev" />
      </div>
      <div className="flex flex-col gap-2 sm:col-span-2">
        <h3 className="text-sm font-medium">Laya (local fallback when Jev gives no answer)</h3>
        <div className="grid gap-3 sm:grid-cols-2">
          <ConfigField name="laya_timeout_ms" label="Laya timeout (ms)">
            {(control) => (
              <input {...control} type="number" step={1} value={laya.timeout_ms} onChange={(event) => setLaya({ timeout_ms: event.target.value })} />
            )}
          </ConfigField>
          {(["t_chat", "t_field", "t_type", "t_link"] as const).map((key) => (
            <ConfigField key={key} name={`laya_${key}`} label={`Laya ${key} (0 to 1)`}>
              {(control) => <input {...control} inputMode="decimal" value={laya[key]} onChange={(event) => setLaya({ [key]: event.target.value })} />}
            </ConfigField>
          ))}
        </div>
        <CheckField label="Laya enabled" checked={laya.enabled} onChange={(enabled) => setLaya({ enabled })} />
        <FieldErrors name="laya" />
      </div>
      <ConfigField
        name="llm"
        label="LLM registry (JSON)"
        hint="Roles, chains, prices, params, concurrency. The server validates everything except JSON syntax."
        className="sm:col-span-2"
      >
        {({ className, ...control }) => (
          <>
            <textarea
              {...control}
              spellCheck={false}
              rows={12}
              className={`${className} h-auto min-h-40 py-1.5 font-mono text-xs`}
              value={draft.llm}
              onChange={(event) => patch({ llm: event.target.value })}
            />
            {jsonError ? (
              <p role="alert" className="text-xs text-destructive">
                {jsonError}
              </p>
            ) : null}
          </>
        )}
      </ConfigField>
      <CheckField label="Note guard enabled" checked={draft.note_guard_enabled} onChange={(note_guard_enabled) => patch({ note_guard_enabled })} />
      <ScalarField name="llm_monthly_budget_usd" label="LLM monthly budget (USD)" />
    </Section>
  )
}
