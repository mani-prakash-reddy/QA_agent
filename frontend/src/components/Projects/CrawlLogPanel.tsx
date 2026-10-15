import { useQuery } from "@tanstack/react-query"
import { useState } from "react"

import { KnowledgeService, RunsService } from "@/client"
import { Badge } from "@/components/ui/badge"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { humanReason } from "@/lib/reasons"

const select =
  "h-9 rounded-md border border-input bg-transparent px-3 text-sm shadow-xs"
export function CrawlLogPanel({ projectId }: { projectId: string }) {
  const [runId, setRunId] = useState<string>("")
  const [status, setStatus] = useState("")
  const { data: runs } = useQuery({
    queryKey: ["runs", projectId],
    queryFn: async () =>
      (await RunsService.listRuns({ path: { project_id: projectId } })).data,
  })
  const crawls = (runs ?? []).filter((r) => r.kind === "crawl")
  const active = runId || crawls[0]?.id

  const { data: log } = useQuery({
    queryKey: ["crawl-log", active, status],
    queryFn: async () =>
      (
        await KnowledgeService.crawlLog({
          path: { run_id: active as string },
          query: { status: status || undefined, limit: 500 },
        })
      ).data,
    enabled: !!active,
  })

  if (crawls.length === 0) {
    return (
      <div className="rounded-md border p-8 text-center text-muted-foreground">
        No crawl yet.
      </div>
    )
  }
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <select
          aria-label="Crawl run"
          className={select}
          value={active}
          onChange={(e) => setRunId(e.target.value)}
        >
          {crawls.map((r) => (
            <option key={r.id} value={r.id}>
              {new Date(r.created_at).toLocaleString()} · {r.status}
            </option>
          ))}
        </select>
        <select
          aria-label="Status"
          className={select}
          value={status}
          onChange={(e) => setStatus(e.target.value)}
        >
          <option value="">all</option>
          <option value="crawled">crawled</option>
          <option value="skipped">skipped</option>
          <option value="blocked">blocked</option>
          <option value="error">error</option>
        </select>
      </div>
      {log && (
        <div className="flex flex-wrap gap-2">
          {log.summary
            .filter((s) => s.status !== "crawled")
            .map((s) => (
              <Badge
                key={`${s.status}-${s.reason}`}
                variant={s.status === "blocked" ? "destructive" : "secondary"}
                title={humanReason(String(s.reason))}
              >
                {String(s.status)}:{" "}
                {String(s.reason ?? "").replace(/^denylist:.*/, "denylist")} ×{" "}
                {Number(s.count)}
              </Badge>
            ))}
        </div>
      )}
      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Status</TableHead>
              <TableHead>Why</TableHead>
              <TableHead>Role</TableHead>
              <TableHead>Action</TableHead>
              <TableHead>Element / URL</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(log?.events ?? []).map((e) => (
              <TableRow key={e.id}>
                <TableCell>
                  <Badge
                    variant={
                      e.status === "blocked"
                        ? "destructive"
                        : e.status === "skipped"
                          ? "secondary"
                          : "outline"
                    }
                  >
                    {e.status}
                  </Badge>
                </TableCell>
                <TableCell className="text-sm">
                  {humanReason(e.reason)}
                </TableCell>
                <TableCell>{e.role ?? ""}</TableCell>
                <TableCell>{e.action ?? ""}</TableCell>
                <TableCell className="max-w-xs truncate text-muted-foreground">
                  {e.element_label || e.url}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}
