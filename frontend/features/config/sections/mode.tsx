"use client"

import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select"
import { ConfigField, Section, useConfigForm } from "@/features/config/fields"
import { TRADING_MODES } from "@/features/config/types"

export function ModeSection() {
  const { draft, patch } = useConfigForm()
  return (
    <Section title="Trading mode" critical description="Decides where approved orders go. A change summary is shown before saving.">
      <ConfigField name="trading_mode" label="Trading mode">
        {(control) => (
          <NativeSelect {...control} value={draft.trading_mode} onChange={(event) => patch({ trading_mode: event.target.value })}>
            {TRADING_MODES.map((mode) => (
              <NativeSelectOption key={mode} value={mode}>
                {mode}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        )}
      </ConfigField>
    </Section>
  )
}
