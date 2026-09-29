"use client"

import { ScalarField, Section } from "@/features/config/fields"

export function RoutingSection() {
  return (
    <Section title="Routing thresholds">
      <ScalarField name="t_chat" label="Chat threshold" hint="0 to 1" />
      <ScalarField name="t_field" label="Field threshold" hint="0 to 1" />
      <ScalarField name="t_link" label="Link threshold" hint="0 to 1" />
    </Section>
  )
}
