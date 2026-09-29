"use client"

import { ScalarField, Section } from "@/features/config/fields"

export function ExpirySection() {
  return (
    <Section title="Expiry cutoffs">
      <ScalarField name="cutoff_1_min_before_close" label="Cutoff 1 (min before close)" />
      <ScalarField name="cutoff_2_min_before_close" label="Cutoff 2 (min before close)" />
    </Section>
  )
}
