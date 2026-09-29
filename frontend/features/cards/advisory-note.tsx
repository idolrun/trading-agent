/** C80: the provider-note is context for the user, never an instruction. Hidden notes are not shown. */
export function AdvisoryNote({ note, hidden }: { note: string | null; hidden: boolean }) {
  if (hidden || !note) return null

  return (
    <aside className="rounded-md border border-dashed border-border px-3 py-2 text-sm">
      <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Advisory</p>
      <p>{note}</p>
    </aside>
  )
}
