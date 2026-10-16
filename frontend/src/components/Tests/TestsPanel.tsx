import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useNavigate } from "@tanstack/react-router"
import { CheckCheck, Sparkles } from "lucide-react"
import { useState } from "react"

import { TestsService } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"
import { ExportMenu } from "./ExportMenu"
import { PushDialog } from "./PushDialog"
import { RtmPanel } from "./RtmPanel"
import { TemplateDialog } from "./TemplateDialog"
import { TestCaseSheet } from "./TestCaseSheet"

const select =
  "h-9 rounded-md border border-input bg-transparent px-3 text-sm shadow-xs"

export const TEST_TYPES = [
  "positive",
  "negative",
  "boundary",
  "field_validation",
  "role_access",
  "e2e",
  "business_rule",
]
export const typeName = (t: string) => t.replace("_", " ")

const PRIORITY: Record<string, string> = {
  P1: "border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-400",
  P2: "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400",
  P3: "text-muted-foreground",
  P4: "text-muted-foreground",
}
const STATUS: Record<string, string> = {
  draft: "bg-muted text-muted-foreground",
  approved: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  rejected: "bg-zinc-500/15 text-zinc-500 line-through",
  edited: "bg-sky-500/15 text-sky-600 dark:text-sky-400",
  needs_regen: "bg-amber-500/15 text-amber-700 dark:text-amber-400",
}

export function PriorityBadge({ p }: { p: string }) {
  return (
    <Badge variant="outline" className={PRIORITY[p] ?? ""}>
      {p}
    </Badge>
  )
}
export function StatusBadge({ s }: { s: string }) {
  return (
    <Badge
      variant="outline"
      className={`border-transparent font-medium ${STATUS[s] ?? ""}`}
    >
      {s.replace("_", " ")}
    </Badge>
  )
}

export function TestsPanel({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { showErrorToast } = useCustomToast()
  const [type, setType] = useState("")
  const [status, setStatus] = useState("")
  const [origin, setOrigin] = useState("")
  const [q, setQ] = useState("")
  const [open, setOpen] = useState<string | null>(null)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [view, setView] = useState("cases")
  const { showSuccessToast } = useCustomToast()

  const { data: summary } = useQuery({
    queryKey: ["tests", projectId, "summary"],
    queryFn: async () =>
      (await TestsService.testsSummary({ path: { project_id: projectId } }))
        .data,
  })
  const { data: page } = useQuery({
    queryKey: ["tests", projectId, type, status, origin, q],
    queryFn: async () =>
      (
        await TestsService.listTests({
          path: { project_id: projectId },
          query: {
            test_type: type || undefined,
            status: status || undefined,
            origin: origin || undefined,
            q: q || undefined,
            limit: 1000,
          },
        })
      ).data,
  })
  const start = useMutation({
    mutationFn: () =>
      TestsService.startGeneration({
        path: { project_id: projectId },
        body: {},
      }),
    onSuccess: ({ data }) => {
      queryClient.invalidateQueries({ queryKey: ["runs", projectId] })
      navigate({ to: "/runs/$runId", params: { runId: data.id } })
    },
    onError: handleError.bind(showErrorToast),
  })

  const bulk = useMutation({
    mutationFn: (action: "approve" | "reject") =>
      TestsService.bulkStatus({
        path: { project_id: projectId },
        body: { ids: [...picked], action },
      }),
    onSuccess: ({ data }) => {
      showSuccessToast(`${(data as { updated: number }).updated} updated`)
      setPicked(new Set())
      queryClient.invalidateQueries({ queryKey: ["tests", projectId] })
      queryClient.invalidateQueries({ queryKey: ["rtm", projectId] })
    },
    onError: handleError.bind(showErrorToast),
  })

  const rows = page?.data ?? []
  const allPicked = rows.length > 0 && rows.every((r) => picked.has(r.id))
  const has = (summary?.total ?? 0) > 0

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="max-w-2xl text-sm text-muted-foreground">
          Test cases are written for every requirement that is mapped to a
          screen. Steps use the real labels found by the crawl; each case shows
          how its expected result is known. Re-generating replaces unreviewed
          drafts and keeps anything you approved.
        </p>
        <div className="flex gap-2">
          {has && <TemplateDialog projectId={projectId} />}
          {has && <ExportMenu projectId={projectId} />}
          {has && <PushDialog projectId={projectId} />}
          <Button onClick={() => start.mutate()} disabled={start.isPending}>
            <Sparkles className="mr-2 h-4 w-4" />
            {has ? "Generate again" : "Generate test cases"}
          </Button>
        </div>
      </div>

      {summary && has && (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Badge variant="secondary">{summary.total} test cases</Badge>
          <Badge variant="outline" className="border-emerald-500/40">
            {summary.requirements_covered} of {summary.requirements_total}{" "}
            requirements covered
          </Badge>
          {Object.entries(summary.by_status).map(([k, v]) => (
            <Badge key={k} variant="outline">
              {v} {k}
            </Badge>
          ))}
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

      {has && (
        <Tabs value={view} onValueChange={setView}>
          <TabsList>
            <TabsTrigger value="cases">Test cases</TabsTrigger>
            <TabsTrigger value="rtm">Traceability matrix</TabsTrigger>
          </TabsList>
          <TabsContent value="rtm" className="pt-3">
            <RtmPanel projectId={projectId} />
          </TabsContent>
          <TabsContent value="cases" className="space-y-3 pt-3">
            <div className="flex flex-wrap items-center gap-2">
              <Input
                aria-label="Search test cases"
                placeholder="Search title or ID"
                className="w-56"
                value={q}
                onChange={(e) => setQ(e.target.value)}
              />
              <select
                aria-label="Type"
                className={select}
                value={type}
                onChange={(e) => setType(e.target.value)}
              >
                <option value="">All types</option>
                {TEST_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {typeName(t)}
                  </option>
                ))}
              </select>
              <select
                aria-label="Status"
                className={select}
                value={status}
                onChange={(e) => setStatus(e.target.value)}
              >
                <option value="">Any status</option>
                {["draft", "approved", "rejected"].map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
              <select
                aria-label="Origin"
                className={select}
                value={origin}
                onChange={(e) => setOrigin(e.target.value)}
              >
                <option value="">Rules and scenarios</option>
                <option value="rule">rule-based</option>
                <option value="llm">scenario (model-written)</option>
              </select>
              <span className="text-sm text-muted-foreground">
                {page?.count ?? 0} shown
              </span>
              {picked.size > 0 && (
                <>
                  <Button size="sm" onClick={() => bulk.mutate("approve")}>
                    <CheckCheck className="mr-1 h-4 w-4" /> Approve{" "}
                    {picked.size}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => bulk.mutate("reject")}
                  >
                    Reject {picked.size}
                  </Button>
                </>
              )}
            </div>

            {!has && (
              <p className="text-sm text-muted-foreground">
                Nothing generated yet. Run mapping first, then generate.
              </p>
            )}

            <div className="rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-8">
                      <Checkbox
                        aria-label="Select all"
                        checked={allPicked}
                        onCheckedChange={(c) =>
                          setPicked(
                            c ? new Set(rows.map((r) => r.id)) : new Set(),
                          )
                        }
                      />
                    </TableHead>
                    <TableHead>ID</TableHead>
                    <TableHead>Title</TableHead>
                    <TableHead>Requirement</TableHead>
                    <TableHead>Type</TableHead>
                    <TableHead>Priority</TableHead>
                    <TableHead>Role</TableHead>
                    <TableHead>Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((t) => (
                    <TableRow
                      key={t.id}
                      className="cursor-pointer"
                      onClick={() => setOpen(t.id)}
                    >
                      <TableCell onClick={(e) => e.stopPropagation()}>
                        <Checkbox
                          aria-label={`Select ${t.tc_key}`}
                          checked={picked.has(t.id)}
                          onCheckedChange={(c) =>
                            setPicked((p) => {
                              const n = new Set(p)
                              if (c) n.add(t.id)
                              else n.delete(t.id)
                              return n
                            })
                          }
                        />
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        <button
                          type="button"
                          className="underline-offset-2 hover:underline"
                          onClick={() => setOpen(t.id)}
                        >
                          {t.tc_key}
                        </button>
                      </TableCell>
                      <TableCell className="max-w-md">
                        <span className="line-clamp-2">{t.title}</span>
                        {t.evidence?.basis === "inferred" ||
                        t.evidence?.basis === "not_observed" ? (
                          <span className="text-xs text-amber-700 dark:text-amber-400">
                            expected result is{" "}
                            {String(t.evidence.basis).replace("_", " ")}
                          </span>
                        ) : null}
                      </TableCell>
                      <TableCell className="whitespace-nowrap">
                        {t.req_key}
                        {t.also_covers.length > 0 && (
                          <span className="text-xs text-muted-foreground">
                            {" "}
                            +{t.also_covers.length}
                          </span>
                        )}
                      </TableCell>
                      <TableCell>
                        <Badge variant="secondary">
                          {typeName(t.test_type)}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <PriorityBadge p={t.priority} />
                      </TableCell>
                      <TableCell className="whitespace-nowrap">
                        {t.role_name ?? "—"}
                      </TableCell>
                      <TableCell>
                        <StatusBadge s={t.status} />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </TabsContent>
        </Tabs>
      )}
      <TestCaseSheet
        testId={open}
        projectId={projectId}
        onClose={() => setOpen(null)}
      />
    </div>
  )
}
