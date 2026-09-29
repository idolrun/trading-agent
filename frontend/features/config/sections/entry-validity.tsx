"use client"

import { ScalarField, Section } from "@/features/config/fields"

export function EntryValiditySection() {
  return (
    <Section title="Entry validity">
      <ScalarField name="entry_chase_pct" label="Chase limit (%)" />
      <ScalarField name="entry_validity_s" label="Card validity (s)" />
      <ScalarField name="entry_cancel_after_s" label="Cancel unfilled after (s)" />
    </Section>
  )
}
