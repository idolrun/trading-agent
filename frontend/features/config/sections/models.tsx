"use client"

import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { CheckField, ConfigField, FieldErrors, ScalarField, Section, useConfigForm } from "@/features/config/fields"

export function ModelsSection() {
  const { draft, patch } = useConfigForm()
  const { jev } = draft
  const setJev = (change: Partial<typeof jev>) => patch({ jev: { ...jev, ...change } })
  const { laya } = draft
  const setLaya = (change: Partial<typeof laya>) => patch({ laya: { ...laya, ...change } })
  return (
    <Section title="Models and LLM" description="Interpreters, the LLM registry and spend limit.">
      <div className="flex flex-col gap-2 sm:col-span-2">
        <h4 className="text-sm font-medium">Jev (primary interpreter)</h4>
        <div className="grid gap-3 sm:grid-cols-2">
          <ConfigField name="jev_model" label="Jev model">
            {(control) => <Input {...control} value={jev.model} onChange={(event) => setJev({ model: event.target.value })} />}
          </ConfigField>
          <ConfigField name="jev_timeout_ms" label="Jev request timeout (ms)">
            {(control) => (
              <Input {...control} type="number" step={1} value={jev.timeout_ms} onChange={(event) => setJev({ timeout_ms: event.target.value })} />
            )}
          </ConfigField>
          <ConfigField name="jev_queue_timeout_ms" label="Jev queue wait (ms)">
            {(control) => (
              <Input {...control} type="number" step={1} value={jev.queue_timeout_ms} onChange={(event) => setJev({ queue_timeout_ms: event.target.value })} />
            )}
          </ConfigField>
          <ConfigField name="jev_budget_ms" label="Jev step budget (ms)">
            {(control) => (
              <Input {...control} type="number" step={1} value={jev.budget_ms} onChange={(event) => setJev({ budget_ms: event.target.value })} />
            )}
          </ConfigField>
        </div>
        <CheckField name="jev_enabled" label="Jev enabled" checked={jev.enabled} onChange={(enabled) => setJev({ enabled })} />
        <FieldErrors name="jev" />
      </div>
      <div className="flex flex-col gap-2 sm:col-span-2">
        <h4 className="text-sm font-medium">Laya (local fallback when Jev gives no usable answer)</h4>
        <div className="grid gap-3 sm:grid-cols-2">
          <ConfigField name="laya_timeout_ms" label="Laya timeout (ms)">
            {(control) => (
              <Input {...control} type="number" step={1} value={laya.timeout_ms} onChange={(event) => setLaya({ timeout_ms: event.target.value })} />
            )}
          </ConfigField>
          {(["t_chat", "t_field", "t_type", "t_link"] as const).map((key) => (
            <ConfigField key={key} name={`laya_${key}`} label={`Laya ${key} (0 to 1)`}>
              {(control) => <Input {...control} inputMode="decimal" value={laya[key]} onChange={(event) => setLaya({ [key]: event.target.value })} />}
            </ConfigField>
          ))}
        </div>
        <CheckField name="laya_enabled" label="Laya enabled" checked={laya.enabled} onChange={(enabled) => setLaya({ enabled })} />
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
            <Textarea
              {...control}
              spellCheck={false}
              rows={12}
              className={`${className} min-h-40 font-mono text-xs`}
              value={draft.llm}
              onChange={(event) => patch({ llm: event.target.value })}
            />
          </>
        )}
      </ConfigField>
      <CheckField name="note_guard_enabled" label="Note guard enabled" checked={draft.note_guard_enabled} onChange={(note_guard_enabled) => patch({ note_guard_enabled })} />
      <ScalarField name="llm_monthly_budget_usd" label="LLM monthly budget (USD)" />
    </Section>
  )
}
