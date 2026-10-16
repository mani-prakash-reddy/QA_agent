import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useNavigate } from "@tanstack/react-router"
import { Check, Download, Link2, Play, Undo2, X } from "lucide-react"
import { useState } from "react"

import {
  type GapPublic,
  KnowledgeService,
  type MappingPublic,
  MappingService,
  RequirementsService,
} from "@/client"
import { StateSheet } from "@/components/Projects/StateSheet"
import { TypeBadge } from "@/components/Requirements/badges"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import useCustomToast from "@/hooks/useCustomToast"
import { apiBase, authHeaders } from "@/lib/runStream"
import { handleError } from "@/utils"

const select =
  "h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm shadow-xs"

const pct = (n: number) => `${Math.round(n * 100)}%`

async function downloadGaps(projectId: string, format: "csv" | "json") {
  const r = await fetch(
    `${apiBase()}/api/v1/projects/${projectId}/gaps/export?format=${format}`,
    { headers: authHeaders() },
  )
  const url = URL.createObjectURL(await r.blob())
  const a = document.createElement("a")
  a.href = url
  a.download = `gap-report.${format}`
  a.click()
  URL.revokeObjectURL(url)
}

function Confidence({
  value,
  effective,
}: {
  value: number
  effective: boolean
}) {
  return (
    <span
      title={
        effective
          ? "Counts as a match"
          : "Below the match threshold: shown for review, not counted"
      }
      className={`rounded px-1.5 py-0.5 text-xs font-medium tabular-nums ${
        effective
          ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400"
          : "bg-amber-500/15 text-amber-700 dark:text-amber-400"
      }`}
    >
      {pct(value)}
    </span>
  )
}

const SEVERITY: Record<string, string> = {
  high: "border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-400",
  medium:
    "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400",
  low: "text-muted-foreground",
}

const CODE_LABEL: Record<string, string> = {
  judged_absent: "no screen shows it",
  no_candidate: "nothing similar in the app",
  not_ui_observable: "not visible in the UI",
  not_assessed: "not assessed yet",
  undocumented_screen: "no requirement",
}

type Linking = { requirementId?: string; stateId?: string }

function LinkDialog({
  projectId,
  snapshotId,
  fixed,
  onClose,
}: {
  projectId: string
  snapshotId: string | null
  fixed: Linking | null
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const [pick, setPick] = useState("")
  const [note, setNote] = useState("")
  const pickScreen = !!fixed?.requirementId

  const { data: states } = useQuery({
    queryKey: ["states", snapshotId, "link"],
    queryFn: async () =>
      (
        await KnowledgeService.listStates({
          path: { snapshot_id: snapshotId as string },
        })
      ).data,
    enabled: !!snapshotId && pickScreen,
  })
  const { data: reqs } = useQuery({
    queryKey: ["requirements", projectId, "link"],
    queryFn: async () =>
      (
        await RequirementsService.listRequirements({
          path: { project_id: projectId },
          query: { limit: 500 },
        })
      ).data,
    enabled: !pickScreen,
  })
  const options = pickScreen
    ? [
        ...new Map(
          (states ?? [])
            .filter((s) => ["page", "modal", "tab"].includes(s.kind))
            .map((s) => [`${s.kind}|${s.route_template}|${s.title}`, s]),
        ).values(),
      ].map((s) => ({
        id: s.id,
        label: `${s.title || "(untitled)"} - ${s.route_template ?? s.kind}`,
      }))
    : (reqs?.data ?? [])
        .filter((r) => r.review_status !== "rejected")
        .map((r) => ({ id: r.id, label: `${r.req_key} - ${r.title}` }))

  const link = useMutation({
    mutationFn: () =>
      MappingService.addMapping({
        path: { project_id: projectId },
        body: {
          requirement_id: (pickScreen ? fixed?.requirementId : pick) as string,
          state_id: (pickScreen ? pick : fixed?.stateId) as string,
          note,
        },
      }),
    onSuccess: () => {
      showSuccessToast("Linked. Gaps were updated.")
      queryClient.invalidateQueries({ queryKey: ["mapping", projectId] })
      setPick("")
      setNote("")
      onClose()
    },
    onError: handleError.bind(showErrorToast),
  })

  return (
    <Dialog open={!!fixed} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {pickScreen ? "Link to a screen" : "Link to a requirement"}
          </DialogTitle>
          <DialogDescription>
            A link you add is treated as confirmed and is kept when mapping runs
            again.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <select
            className={select}
            aria-label={pickScreen ? "Screen" : "Requirement"}
            value={pick}
            onChange={(e) => setPick(e.target.value)}
          >
            <option value="">Choose…</option>
            {options.map((o) => (
              <option key={o.id} value={o.id}>
                {o.label}
              </option>
            ))}
          </select>
          <Input
            aria-label="Note"
            placeholder="Why (optional)"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            disabled={!pick || link.isPending}
            onClick={() => link.mutate()}
          >
            Link
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

type Review = "confirmed" | "rejected" | "proposed"

function MappingRow({
  m,
  onOpen,
  onReview,
}: {
  m: MappingPublic
  onOpen: (stateId: string) => void
  onReview: (id: string, status: Review) => void
}) {
  const citations = (m.citations as { label?: string }[])
    .map((c) => c.label)
    .filter(Boolean)
  return (
    <li
      className={`rounded-md border p-2 text-sm ${m.review_status === "rejected" ? "opacity-60" : ""}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Confidence value={m.confidence} effective={m.effective} />
          <button
            type="button"
            className="font-medium underline-offset-2 hover:underline"
            onClick={() => onOpen(m.state_id)}
          >
            {m.screen_title}
          </button>
          <span className="text-xs text-muted-foreground">
            {m.screen_route ?? m.screen_kind}
          </span>
          {m.method === "manual" && (
            <Badge variant="outline">added by hand</Badge>
          )}
          {m.review_status !== "proposed" && (
            <Badge
              variant="outline"
              className={
                m.review_status === "confirmed"
                  ? "border-emerald-500/40"
                  : "border-red-500/40 text-red-600"
              }
            >
              {m.review_status}
            </Badge>
          )}
        </div>
        <div className="flex gap-1">
          {m.review_status === "proposed" ? (
            <>
              <Button
                size="sm"
                variant="ghost"
                aria-label={`Confirm ${m.req_key} on ${m.screen_title}`}
                onClick={() => onReview(m.id, "confirmed")}
              >
                <Check className="mr-1 h-4 w-4" /> Confirm
              </Button>
              <Button
                size="sm"
                variant="ghost"
                aria-label={`Reject ${m.req_key} on ${m.screen_title}`}
                onClick={() => onReview(m.id, "rejected")}
              >
                <X className="mr-1 h-4 w-4" /> Reject
              </Button>
            </>
          ) : (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => onReview(m.id, "proposed")}
            >
              <Undo2 className="mr-1 h-4 w-4" /> Undo
            </Button>
          )}
        </div>
      </div>
      <p className="mt-1 text-muted-foreground">{m.rationale}</p>
      {citations.length > 0 && (
        <p className="mt-1 text-xs">
          <span className="text-muted-foreground">
            Evidence on the screen:{" "}
          </span>
          {citations.map((c) => (
            <code key={c} className="mr-1 rounded bg-muted px-1">
              {c}
            </code>
          ))}
        </p>
      )}
    </li>
  )
}

function GapList({
  gaps,
  empty,
  onStatus,
  onLink,
  onOpen,
}: {
  gaps: GapPublic[]
  empty: string
  onStatus: (id: string, status: "open" | "accepted") => void
  onLink: (g: GapPublic) => void
  onOpen: (stateId: string) => void
}) {
  if (gaps.length === 0)
    return <p className="text-sm text-muted-foreground">{empty}</p>
  return (
    <ul className="divide-y rounded-md border">
      {gaps.map((g) => (
        <li
          key={g.id}
          className={`flex flex-wrap items-start justify-between gap-2 p-3 text-sm ${g.status === "accepted" ? "opacity-60" : ""}`}
        >
          <div className="min-w-0 space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="outline" className={SEVERITY[g.severity]}>
                {g.severity}
              </Badge>
              {g.direction === "requirement_unmatched" ? (
                <>
                  <span className="font-medium">{g.req_key}</span>
                  <span>{g.requirement_title}</span>
                  {g.requirement_type && (
                    <TypeBadge type={g.requirement_type} />
                  )}
                </>
              ) : (
                <>
                  <button
                    type="button"
                    className="font-medium underline-offset-2 hover:underline"
                    onClick={() => g.state_id && onOpen(g.state_id)}
                  >
                    {g.screen_title ?? "(untitled)"}
                  </button>
                  <span className="text-xs text-muted-foreground">
                    {g.screen_route}
                  </span>
                </>
              )}
              <Badge variant="secondary">{CODE_LABEL[g.code] ?? g.code}</Badge>
              {g.status === "accepted" && (
                <Badge variant="outline">accepted</Badge>
              )}
            </div>
            <p className="text-muted-foreground">{g.reason}</p>
            {Array.isArray(g.evidence?.nearest) && (
              <p className="text-xs text-muted-foreground">
                Nearest screens:{" "}
                {(g.evidence.nearest as { screen: string }[])
                  .map((n) => n.screen)
                  .join(", ")}
              </p>
            )}
          </div>
          <div className="flex gap-1">
            <Button size="sm" variant="ghost" onClick={() => onLink(g)}>
              <Link2 className="mr-1 h-4 w-4" />
              {g.direction === "requirement_unmatched"
                ? "Link a screen"
                : "Link a requirement"}
            </Button>
            {g.status === "open" ? (
              <Button
                size="sm"
                variant="ghost"
                title="Record that this gap is expected and stop counting it as open"
                onClick={() => onStatus(g.id, "accepted")}
              >
                Accept
              </Button>
            ) : (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => onStatus(g.id, "open")}
              >
                Reopen
              </Button>
            )}
          </div>
        </li>
      ))}
    </ul>
  )
}

export function MappingPanel({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { showErrorToast } = useCustomToast()
  const [sheet, setSheet] = useState<string | null>(null)
  const [linking, setLinking] = useState<Linking | null>(null)
  const [view, setView] = useState("matches")
  const key = ["mapping", projectId]

  const { data: summary } = useQuery({
    queryKey: [...key, "summary"],
    queryFn: async () =>
      (await MappingService.mappingSummary({ path: { project_id: projectId } }))
        .data,
  })
  const { data: mappings } = useQuery({
    queryKey: [...key, "mappings"],
    queryFn: async () =>
      (await MappingService.listMappings({ path: { project_id: projectId } }))
        .data,
  })
  const { data: gaps } = useQuery({
    queryKey: [...key, "gaps"],
    queryFn: async () =>
      (await MappingService.listGaps({ path: { project_id: projectId } })).data,
  })
  const { data: reqs } = useQuery({
    queryKey: ["requirements", projectId, "mapping-view"],
    queryFn: async () =>
      (
        await RequirementsService.listRequirements({
          path: { project_id: projectId },
          query: { limit: 500 },
        })
      ).data,
  })

  const refresh = () => queryClient.invalidateQueries({ queryKey: key })
  const start = useMutation({
    mutationFn: () =>
      MappingService.startMapping({
        path: { project_id: projectId },
        body: {},
      }),
    onSuccess: ({ data }) => {
      queryClient.invalidateQueries({ queryKey: ["runs", projectId] })
      navigate({ to: "/runs/$runId", params: { runId: data.id } })
    },
    onError: handleError.bind(showErrorToast),
  })
  const review = useMutation({
    mutationFn: (v: { id: string; status: Review }) =>
      MappingService.reviewMapping({
        path: { mapping_id: v.id },
        body: { review_status: v.status },
      }),
    onSuccess: refresh,
    onError: handleError.bind(showErrorToast),
  })
  const gapStatus = useMutation({
    mutationFn: (v: { id: string; status: "open" | "accepted" }) =>
      MappingService.updateGap({
        path: { gap_id: v.id },
        body: { status: v.status },
      }),
    onSuccess: refresh,
    onError: handleError.bind(showErrorToast),
  })

  const hasRun = (summary?.mappings ?? 0) > 0 || (gaps?.length ?? 0) > 0
  const byReq = new Map<string, MappingPublic[]>()
  for (const m of mappings ?? []) {
    byReq.set(m.requirement_id, [...(byReq.get(m.requirement_id) ?? []), m])
  }
  const gapByReq = new Map(
    (gaps ?? [])
      .filter((g) => g.requirement_id)
      .map((g) => [g.requirement_id as string, g]),
  )
  const reqGaps = (gaps ?? []).filter(
    (g) => g.direction === "requirement_unmatched",
  )
  const featGaps = (gaps ?? []).filter(
    (g) => g.direction === "feature_unmentioned",
  )
  const rows = (reqs?.data ?? []).filter((r) => r.review_status !== "rejected")
  const open = (list: GapPublic[]) =>
    list.filter((g) => g.status === "open").length

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="max-w-2xl text-sm text-muted-foreground">
          Links each requirement to the screens that implement it, with the
          evidence found on the screen, and lists what is missing in both
          directions. Nothing here is final until you confirm it.
        </p>
        <div className="flex gap-2">
          {hasRun && (
            <>
              <Button
                variant="outline"
                size="sm"
                onClick={() => downloadGaps(projectId, "csv")}
              >
                <Download className="mr-1 h-4 w-4" /> Gap report CSV
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => downloadGaps(projectId, "json")}
              >
                JSON
              </Button>
            </>
          )}
          <Button onClick={() => start.mutate()} disabled={start.isPending}>
            <Play className="mr-2 h-4 w-4" />
            {hasRun ? "Map again" : "Run mapping"}
          </Button>
        </div>
      </div>

      {summary && !summary.snapshot_id && (
        <div role="status" className="rounded-md border p-3 text-sm">
          There is no finished crawl yet. Start one from the Crawl tab, then map
          the requirements against it.
        </div>
      )}
      {summary?.crawl_partial && hasRun && (
        <div
          role="status"
          className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm"
        >
          <strong>The crawl was partial.</strong> It stopped at its step budget,
          so a requirement gap may only mean that the screen was not reached.
          Crawl further, then map again, before treating these as missing
          features.
        </div>
      )}
      {summary?.snapshot_id && hasRun && (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Badge variant="outline" className="border-emerald-500/40">
            {summary.requirements_matched} of {summary.requirements_total}{" "}
            requirements matched
          </Badge>
          <Badge variant="outline" className="border-amber-500/40">
            {summary.requirement_gaps} requirement gaps
          </Badge>
          <Badge variant="outline" className="border-amber-500/40">
            {summary.feature_gaps} of {summary.screens_total} screens not in any
            requirement
          </Badge>
          <span className="text-xs text-muted-foreground">
            match threshold {pct(summary.threshold)} · {summary.confirmed}{" "}
            confirmed · {summary.rejected} rejected
          </span>
          {summary.last_run_id && (
            <Link
              to="/runs/$runId"
              params={{ runId: summary.last_run_id }}
              className="text-xs underline"
            >
              last run
            </Link>
          )}
        </div>
      )}

      {hasRun && (
        <Tabs value={view} onValueChange={setView}>
          <TabsList>
            <TabsTrigger value="matches">Requirements → screens</TabsTrigger>
            <TabsTrigger value="req-gaps">
              Requirement gaps ({open(reqGaps)})
            </TabsTrigger>
            <TabsTrigger value="feat-gaps">
              Undocumented screens ({open(featGaps)})
            </TabsTrigger>
          </TabsList>
          <TabsContent value="matches" className="space-y-3 pt-3">
            {rows.map((r) => {
              const ms = byReq.get(r.id) ?? []
              const gap = gapByReq.get(r.id)
              return (
                <div key={r.id} className="space-y-2 rounded-md border p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex flex-wrap items-center gap-2 text-sm">
                      <span className="font-medium">{r.req_key}</span>
                      <span>{r.title}</span>
                      <TypeBadge type={r.type} />
                    </div>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setLinking({ requirementId: r.id })}
                    >
                      <Link2 className="mr-1 h-4 w-4" /> Link a screen
                    </Button>
                  </div>
                  {ms.length > 0 && (
                    <ul className="space-y-2">
                      {ms.map((m) => (
                        <MappingRow
                          key={m.id}
                          m={m}
                          onOpen={setSheet}
                          onReview={(id, status) =>
                            review.mutate({ id, status })
                          }
                        />
                      ))}
                    </ul>
                  )}
                  {gap && (
                    <p className="text-sm text-amber-700 dark:text-amber-400">
                      <strong>Gap:</strong> {gap.reason}
                    </p>
                  )}
                </div>
              )
            })}
          </TabsContent>
          <TabsContent value="req-gaps" className="pt-3">
            <GapList
              gaps={reqGaps}
              empty="Every requirement has a matching screen."
              onStatus={(id, status) => gapStatus.mutate({ id, status })}
              onLink={(g) =>
                setLinking({ requirementId: g.requirement_id ?? undefined })
              }
              onOpen={setSheet}
            />
          </TabsContent>
          <TabsContent value="feat-gaps" className="pt-3">
            <GapList
              gaps={featGaps}
              empty="Every crawled screen is covered by a requirement."
              onStatus={(id, status) => gapStatus.mutate({ id, status })}
              onLink={(g) => setLinking({ stateId: g.state_id ?? undefined })}
              onOpen={setSheet}
            />
          </TabsContent>
        </Tabs>
      )}

      <LinkDialog
        projectId={projectId}
        snapshotId={summary?.snapshot_id ?? null}
        fixed={linking}
        onClose={() => setLinking(null)}
      />
      <StateSheet stateId={sheet} onClose={() => setSheet(null)} />
    </div>
  )
}
