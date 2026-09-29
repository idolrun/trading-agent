"use client"

import { ScalarField, Section } from "@/features/config/fields"

export function ContextSection() {
  return (
    <Section title="Context">
      <ScalarField name="context_recent_messages" label="Recent messages in context" />
    </Section>
  )
}
