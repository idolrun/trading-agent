"use client"

import { RowsField, Section } from "@/features/config/fields"

export function SizingSection() {
  return (
    <Section title="Sizing">
      <RowsField name="option_tiers" label="Option tiers" nameLabel="tier" valueLabel="USD" numeric />
      <RowsField name="stock_tiers" label="Stock tiers" nameLabel="tier" valueLabel="USD" numeric />
    </Section>
  )
}
