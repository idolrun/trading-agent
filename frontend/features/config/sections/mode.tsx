"use client"

import { ConfigField, Section, useConfigForm } from "@/features/config/fields"
import { TRADING_MODES } from "@/features/config/types"

export function ModeSection() {
  const { draft, patch } = useConfigForm()
  return (
    <Section title="Mode">
      <ConfigField name="trading_mode" label="Trading mode">
        {({ className, ...control }) => (
          <select {...control} className={className} value={draft.trading_mode} onChange={(event) => patch({ trading_mode: event.target.value })}>
            {TRADING_MODES.map((mode) => (
              <option key={mode} value={mode}>
                {mode}
              </option>
            ))}
          </select>
        )}
      </ConfigField>
    </Section>
  )
}
