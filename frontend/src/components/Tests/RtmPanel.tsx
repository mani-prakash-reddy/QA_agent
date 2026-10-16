import { useQuery } from "@tanstack/react-query"

import { ExportsService } from "@/client"
import { TypeBadge } from "@/components/Requirements/badges"
import { Badge } from "@/components/ui/badge"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

export function RtmPanel({ projectId }: { projectId: string }) {
  const { data } = useQuery({
    queryKey: ["rtm", projectId],
    queryFn: async () =>
      (await ExportsService.readRtm({ path: { project_id: projectId } })).data,
  })
  if (!data) return <p className="text-muted-foreground">Loading…</p>
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Badge variant="outline" className="border-emerald-500/40">
          {data.covered} of {data.requirements} requirements covered
        </Badge>
        {data.uncovered > 0 && (
          <Badge variant="outline" className="border-amber-500/40">
            {data.uncovered} uncovered
          </Badge>
        )}
        {data.needs_decision > 0 && (
          <Badge variant="outline" className="border-amber-500/40">
            {data.needs_decision} need a wording decision
          </Badge>
        )}
        <span className="text-xs text-muted-foreground">
          {data.test_cases} test cases (rejected ones are not counted). A case
          shared by two requirements counts once and shows under the other as
          “+n”.
        </span>
      </div>
      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Requirement</TableHead>
              <TableHead>Screens</TableHead>
              <TableHead>Tests</TableHead>
              <TableHead>By type</TableHead>
              <TableHead>Reviewed</TableHead>
              <TableHead>Coverage</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.rows.map((r) => (
              <TableRow
                key={r.requirement_id}
                className={r.review_status === "rejected" ? "opacity-50" : ""}
              >
                <TableCell className="min-w-64 max-w-sm">
                  <span className="font-medium">{r.req_key}</span> {r.title}{" "}
                  <TypeBadge type={r.type} />
                </TableCell>
                <TableCell>{r.mapped_screens}</TableCell>
                <TableCell>
                  {r.tests_own}
                  {r.tests_linked > 0 && (
                    <span className="text-xs text-muted-foreground">
                      {" "}
                      +{r.tests_linked}
                    </span>
                  )}
                </TableCell>
                <TableCell className="text-xs">
                  {Object.entries(r.by_type)
                    .filter(([, n]) => n > 0)
                    .map(([t, n]) => `${t.replace("_", " ")} ${n}`)
                    .join(" · ") || "—"}
                </TableCell>
                <TableCell className="text-xs">
                  {r.approved} approved · {r.draft} draft
                  {r.needs_regen > 0 && ` · ${r.needs_regen} to regenerate`}
                </TableCell>
                <TableCell>
                  {r.coverage === "covered" ? (
                    <Badge
                      variant="outline"
                      className="border-emerald-500/40 text-emerald-700 dark:text-emerald-400"
                    >
                      covered
                    </Badge>
                  ) : (
                    <span className="text-xs text-amber-700 dark:text-amber-400">
                      {r.reason}
                    </span>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}
