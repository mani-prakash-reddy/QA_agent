import { useMutation } from "@tanstack/react-query"
import {
  Bot,
  Brain,
  Compass,
  FileSearch,
  ListChecks,
  Maximize2,
  Pause,
  Play,
  Radar,
  ScrollText,
  Square,
  Target,
} from "lucide-react"
import { useEffect, useMemo, useRef, useState } from "react"

import { RunsService } from "@/client"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import useCustomToast from "@/hooks/useCustomToast"
import type { AgentView } from "@/hooks/useRunLive"
import { BUDGET_TEXT } from "@/lib/reasons"
import type { RunEvent } from "@/lib/runStream"
import { handleError } from "@/utils"
import { AuthedImage, screenshotPath } from "./AuthedImage"
import { describe } from "./EventFeed"
import { StatusBadge } from "./StatusBadge"

const KIND_ICON: Record<string, typeof Bot> = {
  supervisor: Brain,
  scout: Radar,
  explorer: Compass,
  mapper: Target,
  extractor: FileSearch,
  test_author: ListChecks,
}

const LOG_LINES = 40
const NOISE = new Set(["tool_result", "llm_usage", "screenshot"])

function AgentLog({ events }: { events: RunEvent[] }) {
  const box = useRef<HTMLDivElement>(null)
  const rows = useMemo(
    () =>
      events
        .filter((e) => !NOISE.has(e.type))
        .map((e) => ({ e, row: describe(e) }))
        .filter((r) => r.row !== null)
        .slice(-LOG_LINES),
    [events],
  )
  const last = rows[rows.length - 1]?.e.seq
  // biome-ignore lint/correctness/useExhaustiveDependencies: scroll whenever a new line arrives
  useEffect(() => {
    if (box.current) box.current.scrollTop = box.current.scrollHeight
  }, [last])
  return (
    <div
      ref={box}
      role="log"
      aria-label="Agent log"
      className="h-24 overflow-y-auto rounded-md bg-muted/50 p-1.5 font-mono text-[10.5px] leading-snug"
    >
      {rows.length === 0 && (
        <span className="text-muted-foreground">Waiting…</span>
      )}
      {rows.map(({ e, row }) => (
        <div key={e.seq} className="flex gap-1">
          <span className={`shrink-0 ${row?.tone}`}>{row?.icon}</span>
          <span className={`min-w-0 break-words ${row?.tone}`}>
            {row?.text}
          </span>
        </div>
      ))}
    </div>
  )
}

function AgentCard({
  agent,
  events,
  runId,
  runLive,
  selected,
  onSelect,
}: {
  agent: AgentView
  events: RunEvent[]
  runId: string
  runLive: boolean
  selected: boolean
  onSelect: () => void
}) {
  const [zoom, setZoom] = useState(false)
  const { showErrorToast } = useCustomToast()
  const control = useMutation({
    mutationFn: (action: "pause" | "resume" | "stop") =>
      RunsService.controlRun({
        path: { run_id: runId },
        body: { action, agent_id: agent.id },
      }),
    onError: handleError.bind(showErrorToast),
  })
  const Icon = KIND_ICON[agent.kind] ?? Bot
  const running = agent.status === "running" || agent.status === "authenticated"
  const paused = agent.status === "paused" || agent.status === "pause_requested"
  const mine = useMemo(
    () => events.filter((e) => e.agent_id === agent.id),
    [events, agent.id],
  )
  const shot = agent.lastStateId ? screenshotPath(agent.lastStateId) : null

  return (
    <div
      className={`w-72 rounded-xl border bg-card text-left shadow-sm transition-shadow ${
        selected ? "ring-2 ring-primary" : ""
      } ${running ? "border-emerald-500/60" : ""}`}
    >
      <div className="flex items-center gap-2 border-b px-3 py-2">
        <span
          className={`relative flex size-7 items-center justify-center rounded-md ${
            running ? "bg-emerald-500/15 text-emerald-600" : "bg-muted"
          }`}
        >
          <Icon className="size-4" />
          {running && (
            <span className="absolute -right-0.5 -top-0.5 size-2 animate-pulse rounded-full bg-emerald-500" />
          )}
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium capitalize">
            {agent.kind.replace("_", " ")}
          </div>
          <div
            className="truncate text-[11px] text-muted-foreground"
            title={
              agent.detail ? (BUDGET_TEXT[agent.detail] ?? agent.detail) : ""
            }
          >
            {agent.role ? `${agent.role} · ` : ""}
            {agent.steps} steps
            {agent.detail
              ? ` · ${BUDGET_TEXT[agent.detail] ?? agent.detail}`
              : ""}
          </div>
        </div>
        <StatusBadge status={agent.status} />
      </div>

      <div className="space-y-2 p-2">
        {shot ? (
          <button
            type="button"
            onClick={() => setZoom(true)}
            className="group relative block w-full overflow-hidden rounded-md border"
            aria-label="Enlarge latest screen"
          >
            <AuthedImage
              path={shot}
              alt={`Latest screen seen by ${agent.role ?? agent.kind}`}
              className="aspect-video w-full object-cover object-top"
            />
            <span className="absolute right-1 top-1 rounded bg-background/80 p-1 opacity-0 transition-opacity group-hover:opacity-100">
              <Maximize2 className="size-3" />
            </span>
          </button>
        ) : (
          <div className="flex aspect-video items-center justify-center rounded-md border bg-gradient-to-br from-muted/60 to-muted/10 p-3 text-center text-xs text-muted-foreground">
            <span className="line-clamp-4">
              {agent.goal || "No screen yet"}
            </span>
          </div>
        )}

        <AgentLog events={mine} />

        <div className="flex items-center gap-1">
          <Button
            size="sm"
            variant={selected ? "default" : "outline"}
            className="h-7 flex-1 px-2 text-xs"
            onClick={onSelect}
          >
            <ScrollText className="mr-1 size-3" />
            {selected ? "Showing feed" : "Full feed"}
          </Button>
          {runLive && (running || paused) && (
            <>
              <Button
                size="icon"
                variant="outline"
                className="size-7"
                aria-label={paused ? "Resume agent" : "Pause agent"}
                onClick={() => control.mutate(paused ? "resume" : "pause")}
                disabled={control.isPending}
              >
                {paused ? (
                  <Play className="size-3" />
                ) : (
                  <Pause className="size-3" />
                )}
              </Button>
              <Button
                size="icon"
                variant="outline"
                className="size-7 text-red-600"
                aria-label="Stop agent"
                onClick={() => control.mutate("stop")}
                disabled={control.isPending}
              >
                <Square className="size-3" />
              </Button>
            </>
          )}
        </div>
      </div>

      <Dialog open={zoom} onOpenChange={setZoom}>
        <DialogContent className="sm:max-w-5xl">
          <DialogHeader>
            <DialogTitle className="capitalize">
              {agent.kind} {agent.role ? `· ${agent.role}` : ""}: latest screen
            </DialogTitle>
          </DialogHeader>
          <AuthedImage
            path={shot}
            alt="Latest screen"
            className="w-full rounded-md border"
          />
        </DialogContent>
      </Dialog>
    </div>
  )
}

type Props = {
  agents: Record<string, AgentView>
  order: string[]
  events: RunEvent[]
  runId: string
  runLive: boolean
  selected: string | null
  onSelect: (id: string | null) => void
}

export function AgentGraph({
  agents,
  order,
  events,
  runId,
  runLive,
  selected,
  onSelect,
}: Props) {
  const all = order.map((id) => agents[id])
  const childrenOf: Record<string, AgentView[]> = {}
  for (const a of all) {
    if (a.parentId && agents[a.parentId]) {
      childrenOf[a.parentId] = [...(childrenOf[a.parentId] ?? []), a]
    }
  }
  const roots = all.filter((a) => !a.parentId || !agents[a.parentId])
  const canvas = useRef<HTMLDivElement>(null)
  const rootKey = roots.map((r) => r.id).join(",")
  // biome-ignore lint/correctness/useExhaustiveDependencies: centre the supervisor when the roots change
  useEffect(() => {
    const el = canvas.current
    if (el) el.scrollLeft = Math.max(0, (el.scrollWidth - el.clientWidth) / 2)
  }, [rootKey])

  if (roots.length === 0) {
    return (
      <p className="rounded-md border p-6 text-center text-sm text-muted-foreground">
        Waiting for agents to start…
      </p>
    )
  }

  const node = (a: AgentView) => (
    <li key={a.id}>
      <AgentCard
        agent={a}
        events={events}
        runId={runId}
        runLive={runLive}
        selected={selected === a.id}
        onSelect={() => onSelect(selected === a.id ? null : a.id)}
      />
      {(childrenOf[a.id]?.length ?? 0) > 0 && (
        <ul>{childrenOf[a.id].map(node)}</ul>
      )}
    </li>
  )

  return (
    <div ref={canvas} className="agent-tree overflow-x-auto pb-3">
      <ul className="min-w-max">{roots.map(node)}</ul>
    </div>
  )
}
