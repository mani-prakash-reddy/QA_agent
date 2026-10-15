import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useNavigate } from "@tanstack/react-router"
import { Play } from "lucide-react"

import { ProjectsService, RunsService } from "@/client"
import { StatusBadge } from "@/components/Runs/StatusBadge"
import { Button } from "@/components/ui/button"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import useCustomToast from "@/hooks/useCustomToast"
import { TERMINAL_STATUSES } from "@/lib/runStream"
import { handleError } from "@/utils"

const duration = (
  start: string | null | undefined,
  end: string | null | undefined,
) => {
  if (!start) return "—"
  const ms =
    (end ? new Date(end).getTime() : Date.now()) - new Date(start).getTime()
  const s = Math.max(0, Math.round(ms / 1000))
  return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`
}

export function RunsPanel({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { showErrorToast } = useCustomToast()

  const { data: runs } = useQuery({
    queryKey: ["runs", projectId],
    queryFn: async () =>
      (await RunsService.listRuns({ path: { project_id: projectId } })).data,
    refetchInterval: (q) =>
      q.state.data?.some((r) => !TERMINAL_STATUSES.includes(r.status))
        ? 3000
        : false,
  })
  const { data: roles } = useQuery({
    queryKey: ["roles", projectId],
    queryFn: async () =>
      (await ProjectsService.listRoles({ path: { project_id: projectId } }))
        .data,
  })

  const start = useMutation({
    mutationFn: () =>
      RunsService.createRun({
        path: { project_id: projectId },
        body: { kind: "crawl", params: {} },
      }),
    onSuccess: ({ data }) => {
      queryClient.invalidateQueries({ queryKey: ["runs", projectId] })
      navigate({ to: "/runs/$runId", params: { runId: data.id } })
    },
    onError: handleError.bind(showErrorToast),
  })
  const ready = (roles ?? []).some((r) => r.has_credentials)

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          A crawl signs in as each role and explores the application without
          changing any data.
        </p>
        <Button
          onClick={() => start.mutate()}
          disabled={!ready || start.isPending}
        >
          <Play className="mr-2 h-4 w-4" />
          Start crawl
        </Button>
      </div>
      {!ready && (
        <p className="text-sm text-amber-600 dark:text-amber-400">
          Add a role with credentials first (Roles tab).
        </p>
      )}
      {runs && runs.length > 0 ? (
        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Run</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Started</TableHead>
                <TableHead>Duration</TableHead>
                <TableHead>Result</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {runs.map((r) => {
                const stats = (r.summary as { stats?: Record<string, number> })
                  ?.stats
                return (
                  <TableRow key={r.id}>
                    <TableCell>
                      <Link
                        to="/runs/$runId"
                        params={{ runId: r.id }}
                        className="font-medium underline"
                      >
                        {r.kind}
                      </Link>
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={r.status} />
                    </TableCell>
                    <TableCell>
                      {new Date(r.created_at).toLocaleString()}
                    </TableCell>
                    <TableCell>
                      {duration(r.started_at ?? r.created_at, r.finished_at)}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {stats
                        ? `${stats.states} screens · ${stats.forms} forms · ${stats.blocked} blocked`
                        : (r.error ?? "")}
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </div>
      ) : (
        <div className="rounded-md border p-8 text-center text-muted-foreground">
          No runs yet.
        </div>
      )}
    </div>
  )
}
