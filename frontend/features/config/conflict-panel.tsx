"use client"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"

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
    <Alert className="flex flex-col items-stretch gap-3 border-warning bg-warning/5">
      <AlertTitle>Config changed on the server (now version {version})</AlertTitle>
      <AlertDescription>
        Nothing was saved. Reload discards your edits. Reapply keeps your edited fields on top of the new version.
      </AlertDescription>
      {rows.length > 0 ? (
        <Table aria-label="Conflicting fields">
          <TableHeader>
            <TableRow>
              <TableHead>Field</TableHead>
              <TableHead>Yours</TableHead>
              <TableHead>Server</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={row.key}>
                <TableHead scope="row" className="font-mono text-xs">{row.key}</TableHead>
                <TableCell className="font-mono text-xs break-all">{show(row.mine)}</TableCell>
                <TableCell className="font-mono text-xs break-all">{show(row.server)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : (
        <p className="text-sm">None of your edited fields differ from the server.</p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="outline" onClick={onReload}>
          Reload (discard my edits)
        </Button>
        <Button type="button" onClick={onReapply}>
          Reapply my edits
        </Button>
      </div>
    </Alert>
  )
}
