import { useQuery } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { useEffect, useRef, useState } from "react"

import { RunsService } from "@/client"
import { AgentTree } from "@/components/Runs/AgentTree"
import { EventFeed } from "@/components/Runs/EventFeed"
import { RunControls } from "@/components/Runs/RunControls"
import { ScreenshotPanel } from "@/components/Runs/ScreenshotPanel"
import { StatusBadge } from "@/components/Runs/StatusBadge"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { useRunLive } from "@/hooks/useRunLive"
import { TERMINAL_STATUSES } from "@/lib/runStream"

export const Route = createFileRoute("/_layout/runs/$runId")({
  component: RunPage,
  head: () => ({ meta: [{ title: "Live run - QA Agent" }] }),
})

const fmtDuration = (ms: number) => {
  const s = Math.max(0, Math.floor(ms / 1000))
  return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`
}

function Counter({
  label,
  value,
  tone,
}: {
  label: string
  value: number
  tone?: string
}) {
  return (
    <div className="rounded-md border p-2 text-center">
      <div className={`text-xl font-semibold tabular-nums ${tone ?? ""}`}>
        {value}
      </div>
      <div className="text-xs text-muted-foreground">{label}</div>
    </div>
  )
}

function RunPage() {
  const { runId } = Route.useParams()
  const [selected, setSelected] = useState<string | null>(null)
  const [detail, setDetail] = useState(false)
  const [showSkipped, setShowSkipped] = useState(false)
  const [now, setNow] = useState(() => Date.now())

  const { data: run } = useQuery({
    queryKey: ["run", runId],
    queryFn: async () =>
      (await RunsService.readRun({ path: { run_id: runId } })).data,
    refetchInterval: (q) =>
      q.state.data && TERMINAL_STATUSES.includes(q.state.data.status)
        ? false
        : 2500,
  })

  // Decide live-vs-replay once, from the status the run had when the page opened.
  const firstStatus = useRef<string | undefined>(undefined)
  if (run && firstStatus.current === undefined) firstStatus.current = run.status
  const live = useRunLive(runId, firstStatus.current)

  const status = run?.status ?? live.runStatus
  const active = !TERMINAL_STATUSES.includes(status)
  useEffect(() => {
    if (!active) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [active])

  if (!run) return <p className="text-muted-foreground">Loading run…</p>

  const started = new Date(run.started_at ?? run.created_at).getTime()
  const ended = run.finished_at ? new Date(run.finished_at).getTime() : now
  const shotState = selected
    ? (live.agents[selected]?.lastStateId ?? null)
    : (live.latestShot?.stateId ?? null)
  const snapshotId = (run.summary as { snapshot_id?: string })?.snapshot_id

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="space-y-1">
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold tracking-tight">
              Run · {run.kind}
            </h1>
            <StatusBadge status={status} />
            {!live.connected && active && (
              <Badge variant="outline">reconnecting…</Badge>
            )}
          </div>
          <p className="text-sm text-muted-foreground">
            {fmtDuration(ended - started)} ·{" "}
            {live.tokens.input + live.tokens.output} tokens ·{" "}
            <Link
              to="/projects/$projectId"
              params={{ projectId: run.project_id }}
              className="underline"
            >
              back to project
            </Link>
          </p>
        </div>
        <div className="flex items-center gap-2">
          <RunControls runId={runId} status={status} />
          {run.kind === "ingest" && (
            <Button asChild size="sm" variant="outline">
              <Link
                to="/projects/$projectId"
                params={{ projectId: run.project_id }}
                search={{ tab: "requirements" }}
              >
                Review requirements
              </Link>
            </Button>
          )}
          {snapshotId && (
            <Button asChild size="sm" variant="outline">
              <Link
                to="/projects/$projectId"
                params={{ projectId: run.project_id }}
                search={{ tab: "model", snapshot: snapshotId }}
              >
                View knowledge model
              </Link>
            </Button>
          )}
        </div>
      </div>

      {(run.summary as { partial?: boolean })?.partial && (
        <div
          role="status"
          className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm"
        >
          <strong>Partial result.</strong> The step budget was reached before
          every screen was explored. What was found is saved and usable; raise
          “max steps per agent” in the crawl configuration to explore further.
        </div>
      )}

      {run.error && (
        <div
          role="alert"
          className="rounded-md border border-red-500/40 bg-red-500/10 p-3 text-sm"
        >
          {run.error}
        </div>
      )}

      {run.kind === "ingest" ? (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
          <Counter
            label={`Sections (of ${live.counters.chunksTotal || "…"})`}
            value={live.counters.chunksDone}
          />
          <Counter label="Requirements" value={live.counters.requirements} />
          <Counter
            label="Flagged ambiguous"
            value={Number((run.summary as { flagged?: number })?.flagged ?? 0)}
            tone="text-amber-600 dark:text-amber-400"
          />
          <Counter
            label="Discarded (not in document)"
            value={live.counters.rejected}
            tone="text-red-600 dark:text-red-400"
          />
          <Counter label="Errors" value={live.counters.errors} />
        </div>
      ) : (
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
          <Counter label="Screens" value={live.counters.states} />
          <Counter label="Forms" value={live.counters.forms} />
          <Counter label="Paths" value={live.counters.transitions} />
          <Counter
            label="Skipped"
            value={live.counters.skipped}
            tone="text-amber-600 dark:text-amber-400"
          />
          <Counter
            label="Blocked"
            value={live.counters.blocked}
            tone="text-red-600 dark:text-red-400"
          />
          <Counter label="Actions" value={live.counters.toolCalls} />
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-12">
        <Card className="lg:col-span-3">
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Agents</CardTitle>
          </CardHeader>
          <CardContent>
            <AgentTree
              agents={live.agents}
              order={live.order}
              selected={selected}
              onSelect={setSelected}
            />
          </CardContent>
        </Card>

        <Card className="lg:col-span-5">
          <CardHeader className="flex-row items-center justify-between pb-2">
            <CardTitle className="text-base">
              Live feed
              {selected && live.agents[selected]
                ? ` · ${live.agents[selected].role ?? live.agents[selected].kind}`
                : ""}
            </CardTitle>
            <div className="flex gap-3 text-xs text-muted-foreground">
              <label className="flex items-center gap-1">
                <input
                  type="checkbox"
                  checked={showSkipped}
                  onChange={(e) => setShowSkipped(e.target.checked)}
                />
                Skipped
              </label>
              <label className="flex items-center gap-1">
                <input
                  type="checkbox"
                  checked={detail}
                  onChange={(e) => setDetail(e.target.checked)}
                />
                Tool calls
              </label>
            </div>
          </CardHeader>
          <CardContent>
            <EventFeed
              events={live.events}
              agents={live.agents}
              selected={selected}
              detail={detail}
              showSkipped={showSkipped}
            />
          </CardContent>
        </Card>

        <Card className="lg:col-span-4">
          <CardContent className="pt-6">
            <ScreenshotPanel stateId={shotState} />
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
