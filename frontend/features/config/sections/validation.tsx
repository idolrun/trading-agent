"use client"

import { RowsField, ScalarField, Section } from "@/features/config/fields"

export function ValidationSection() {
  return (
    <Section title="Contract and price validation" description="Checks that run before a signal can become a card.">
      <ScalarField name="identity_price_tolerance_pct" label="Identity price tolerance (%)" />
      <ScalarField name="adjacent_strike_ratio" label="Adjacent strike ratio" />
      <ScalarField name="missing_underlying_proximity_pct" label="Missing underlying proximity (%)" />
      <ScalarField name="missing_underlying_lookback_days" label="Missing underlying lookback (days)" />
      <RowsField name="symbol_map" label="Symbol map" nameLabel="from" valueLabel="to" />
    </Section>
  )
}
