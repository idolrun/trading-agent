"use client"

import { ScalarField, Section } from "@/features/config/fields"

export function ContextSection() {
  return (
    <Section title="Interpretation context" description="What the interpreter sees besides the current message.">
      <ScalarField name="context_recent_messages" label="Recent messages in context" />
    </Section>
  )
}
