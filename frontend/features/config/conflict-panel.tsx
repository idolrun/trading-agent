"use client"

import { Button } from "@/components/ui/button"

export type ConflictRow = { key: string; mine: unknown; server: unknown }

const show = (value: unknown) => JSON.stringify(value) ?? "unset"

/** Shown after a 409: the server's current version next to the fields this session changed. */
export function ConflictPanel({
  version,
  rows,
  onReload,
  onReapply,
}: {
  version: number
  rows: ConflictRow[]
  onReload: () => void
  onReapply: () => void
}) {
  return (
    <div role="alert" className="flex flex-col gap-3 rounded-lg border border-amber-500 bg-amber-500/5 p-4">
      <div>
        <p className="font-medium">Config changed on the server (now version {version})</p>
        <p className="text-sm text-muted-foreground">
          Nothing was saved. Reload discards your edits. Reapply keeps your edited fields on top of the new version.
        </p>
      </div>
      {rows.length > 0 ? (
        <div className="overflow-x-auto rounded-md border border-border">
          <table aria-label="Conflicting fields" className="w-full text-sm">
            <thead className="bg-muted text-left text-xs uppercase tracking-wide text-muted-foreground">
              <tr>
                <th scope="col" className="px-3 py-2 font-medium">Field</th>
                <th scope="col" className="px-3 py-2 font-medium">Yours</th>
                <th scope="col" className="px-3 py-2 font-medium">Server</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {rows.map((row) => (
                <tr key={row.key}>
                  <th scope="row" className="px-3 py-2 text-left font-mono text-xs">{row.key}</th>
                  <td className="px-3 py-2 font-mono text-xs break-all">{show(row.mine)}</td>
                  <td className="px-3 py-2 font-mono text-xs break-all">{show(row.server)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-sm">None of your edited fields differ from the server.</p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="outline" className="h-11" onClick={onReload}>
          Reload (discard my edits)
        </Button>
        <Button type="button" className="h-11" onClick={onReapply}>
          Reapply my edits
        </Button>
      </div>
    </div>
  )
}
