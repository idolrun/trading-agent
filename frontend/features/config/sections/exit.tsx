"use client"

import { ScalarField, Section } from "@/features/config/fields"

export function ExitSection() {
  return (
    <Section title="Exit and re-price" description="How an approved exit order is walked toward the bid.">
      <ScalarField name="exit_card_timeout_s" label="Exit card timeout (s)" />
      <ScalarField name="exit_reprice_interval_s" label="Re-price interval (s)" />
      <ScalarField name="exit_reprice_step_pct" label="Re-price step (%)" />
      <ScalarField name="exit_floor_pct" label="Exit floor (% below mid)" />
    </Section>
  )
}
