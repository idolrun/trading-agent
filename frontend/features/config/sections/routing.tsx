"use client"

import { ScalarField, Section } from "@/features/config/fields"

export function RoutingSection() {
  return (
    <Section title="Routing thresholds" description="Confidence needed before a message is handled automatically instead of going to the slow path or review.">
      <ScalarField name="t_chat" label="Chat threshold" hint="0 to 1" />
      <ScalarField name="t_field" label="Field threshold" hint="0 to 1" />
      <ScalarField name="t_type" label="Message type threshold" hint="0 to 1; confidence needed to act on the type" />
      <ScalarField name="t_link" label="Link threshold" hint="0 to 1" />
    </Section>
  )
}
