import { useQuery } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"

import { ExportsService } from "@/client"
import { StatusBadge } from "@/components/Runs/StatusBadge"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

const fmt = (s: number | null | undefined) =>
  s == null
    ? "—"
    : `${Math.floor(s / 60)}m ${String(Math.round(s % 60)).padStart(2, "0")}s`

export function MetricsPanel({ projectId }: { projectId: string }) {
  const { data } = useQuery({
    queryKey: ["metrics", projectId],
    queryFn: async () =>
      (await ExportsService.metrics({ path: { project_id: projectId } })).data,
  })
  if (!data) return <p className="text-muted-foreground">Loading…</p>
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Time and model tokens per kind of run.{" "}
        {data.input_tokens + data.output_tokens} tokens in total (
        {data.input_tokens} in, {data.output_tokens} out). Cost is not priced:
        tokens are what the provider reports.
      </p>
      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Run kind</TableHead>
              <TableHead>Runs</TableHead>
              <TableHead>Time</TableHead>
              <TableHead>Tokens in</TableHead>
              <TableHead>Tokens out</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.by_kind.map((k) => (
              <TableRow key={k.kind}>
                <TableCell className="font-medium">{k.kind}</TableCell>
                <TableCell>{k.runs}</TableCell>
                <TableCell>{fmt(k.seconds)}</TableCell>
                <TableCell>{k.input_tokens}</TableCell>
                <TableCell>{k.output_tokens}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <h3 className="text-sm font-semibold">Recent runs</h3>
      <div className="rounded-md border">
        <Table>
          <TableBody>
            {data.runs.map((r) => (
              <TableRow key={r.run_id}>
                <TableCell>
                  <Link
                    to="/runs/$runId"
                    params={{ runId: r.run_id }}
                    className="underline"
                  >
                    {r.kind}
                  </Link>
                </TableCell>
                <TableCell>
                  <StatusBadge status={r.status} />
                </TableCell>
                <TableCell>{fmt(r.seconds)}</TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {r.input_tokens + r.output_tokens} tokens
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}
