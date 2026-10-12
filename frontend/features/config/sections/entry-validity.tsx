"use client"

import { ScalarField, Section } from "@/features/config/fields"

export function EntryValiditySection() {
  return (
    <Section title="Entry limits and timing" critical description="Price band, approval window and cancel timer for entries.">
      <ScalarField name="entry_chase_pct" label="Chase limit (%)" />
      <ScalarField name="entry_validity_s" label="Card validity (s)" />
      <ScalarField name="entry_cancel_after_s" label="Cancel unfilled after (s)" />
      <ScalarField name="quote_max_age_s" label="Quote max age (s)" hint="Free Alpaca options data is ~15 min delayed: use about 1200 there." />
    </Section>
  )
}
