"use client"

import { ScalarField, Section } from "@/features/config/fields"

export function RiskSection() {
  return (
    <Section title="Risk limits" critical description="Entries and adds are blocked when a limit is reached or when risk cannot be computed. Exits are never blocked.">
      <ScalarField name="max_daily_loss_usd" label="Max daily loss (USD)" />
      <ScalarField name="max_open_positions" label="Max open positions" />
      <ScalarField name="max_total_exposure_usd" label="Max total exposure (USD)" />
    </Section>
  )
}
