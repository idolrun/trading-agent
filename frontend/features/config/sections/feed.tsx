"use client"

import { ListField, ScalarField, Section } from "@/features/config/fields"

export function FeedSection() {
  return (
    <Section title="Feed">
      <ListField name="channels" label="Channels" />
      <ListField name="muted_authors" label="Muted authors" />
      <ScalarField name="feed_silence_s" label="Feed silence alert (s)" />
      <ScalarField name="stale_entry_s" label="Stale entry (s)" />
      <ScalarField name="catch_up_backlog_n" label="Catch-up backlog (messages)" />
      <ScalarField name="poison_retry_limit" label="Poison retry limit" />
    </Section>
  )
}
