import { useQuery } from "@tanstack/react-query"
import { Check, Download } from "lucide-react"
import { useState } from "react"

import { KnowledgeService } from "@/client"
import { StatusBadge } from "@/components/Runs/StatusBadge"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { apiBase, authHeaders } from "@/lib/runStream"
import { StateSheet } from "./StateSheet"

const KINDS = ["", "page", "modal", "menu", "tab", "listbox"]
const select =
  "h-9 rounded-md border border-input bg-transparent px-3 text-sm shadow-xs"

async function downloadJson(snapshotId: string) {
  const r = await fetch(
    `${apiBase()}/api/v1/snapshots/${snapshotId}/export.json`,
    { headers: authHeaders() },
  )
  const url = URL.createObjectURL(await r.blob())
  const a = document.createElement("a")
  a.href = url
  a.download = `knowledge-model-${snapshotId.slice(0, 8)}.json`
  a.click()
  URL.revokeObjectURL(url)
}

export function ModelPanel({
  projectId,
  snapshotId,
  onSnapshot,
  view,
}: {
  projectId: string
  snapshotId?: string
  onSnapshot: (id: string) => void
  view: "states" | "matrix"
}) {
  const [kind, setKind] = useState("")
  const [q, setQ] = useState("")
  const [open, setOpen] = useState<string | null>(null)

  const { data: snaps } = useQuery({
    queryKey: ["snapshots", projectId],
    queryFn: async () =>
      (
        await KnowledgeService.listSnapshots({
          path: { project_id: projectId },
        })
      ).data,
  })
  const active = snapshotId ?? snaps?.[0]?.id

  const { data: states } = useQuery({
    queryKey: ["states", active, kind, q],
    queryFn: async () =>
      (
        await KnowledgeService.listStates({
          path: { snapshot_id: active as string },
          query: { kind: kind || undefined, q: q || undefined },
        })
      ).data,
    enabled: !!active && view === "states",
  })
  const { data: matrix } = useQuery({
    queryKey: ["matrix", active],
    queryFn: async () =>
      (
        await KnowledgeService.roleMatrix({
          path: { snapshot_id: active as string },
        })
      ).data,
    enabled: !!active && view === "matrix",
  })

  if (snaps && snaps.length === 0) {
    return (
      <div className="rounded-md border p-8 text-center text-muted-foreground">
        No knowledge model yet. Start a crawl first.
      </div>
    )
  }

  const current = snaps?.find((s) => s.id === active)
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <select
          aria-label="Snapshot"
          className={select}
          value={active ?? ""}
          onChange={(e) => onSnapshot(e.target.value)}
        >
          {(snaps ?? []).map((s) => (
            <option key={s.id} value={s.id}>
              {new Date(s.created_at).toLocaleString()} ·{" "}
              {(s.stats as { states?: number }).states ?? 0} screens
            </option>
          ))}
        </select>
        {current && <StatusBadge status={current.status} />}
        {view === "states" && (
          <>
            <select
              aria-label="Screen kind"
              className={select}
              value={kind}
              onChange={(e) => setKind(e.target.value)}
            >
              {KINDS.map((k) => (
                <option key={k} value={k}>
                  {k || "all kinds"}
                </option>
              ))}
            </select>
            <Input
              className="w-56"
              placeholder="Search screens…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
          </>
        )}
        {active && (
          <Button
            size="sm"
            variant="outline"
            className="ml-auto"
            onClick={() => downloadJson(active)}
          >
            <Download className="mr-1 h-4 w-4" /> Export JSON
          </Button>
        )}
      </div>

      {view === "states" && (
        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Screen</TableHead>
                <TableHead>Kind</TableHead>
                <TableHead>Module</TableHead>
                <TableHead>Route</TableHead>
                <TableHead>Roles</TableHead>
                <TableHead className="text-right">Elements</TableHead>
                <TableHead className="text-right">Forms</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(states ?? []).map((s) => (
                <TableRow
                  key={s.id}
                  className="cursor-pointer"
                  onClick={() => setOpen(s.id)}
                >
                  <TableCell className="font-medium">
                    <button
                      type="button"
                      className="text-left underline-offset-2 hover:underline"
                      onClick={() => setOpen(s.id)}
                    >
                      {s.title || "(untitled)"}
                    </button>
                  </TableCell>
                  <TableCell>
                    <Badge variant="secondary">{s.kind}</Badge>
                  </TableCell>
                  <TableCell>{s.module ?? "—"}</TableCell>
                  <TableCell className="text-muted-foreground">
                    {s.route_template}
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1">
                      {s.roles.map((r) => (
                        <Badge key={r} variant="outline">
                          {r}
                        </Badge>
                      ))}
                    </div>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {s.n_elements}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {s.n_forms}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      {view === "matrix" && matrix && (
        <div className="overflow-x-auto rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Screen</TableHead>
                <TableHead>Module</TableHead>
                {matrix.roles.map((r) => (
                  <TableHead key={r} className="text-center">
                    {r}
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {matrix.rows.map((row) => (
                <TableRow key={row.state_id}>
                  <TableCell>
                    {row.title}{" "}
                    <span className="text-xs text-muted-foreground">
                      ({row.kind})
                    </span>
                  </TableCell>
                  <TableCell>{row.module ?? "—"}</TableCell>
                  {matrix.roles.map((r) => (
                    <TableCell key={r} className="text-center">
                      {row.access[r] ? (
                        <Check
                          className="mx-auto h-4 w-4 text-emerald-600"
                          aria-label="can reach"
                        />
                      ) : (
                        <span
                          className="text-muted-foreground"
                          title="not observed"
                        >
                          —
                        </span>
                      )}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <p className="p-3 text-xs text-muted-foreground">
            Based on what each role's crawl actually reached. “—” means not
            observed, not proven forbidden.
          </p>
        </div>
      )}
      <StateSheet stateId={open} onClose={() => setOpen(null)} />
    </div>
  )
}
