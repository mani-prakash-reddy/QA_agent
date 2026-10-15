import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { CheckCheck, FileUp, RefreshCw, Trash2 } from "lucide-react"
import { useEffect, useRef, useState } from "react"

import { BrdService, RequirementsService } from "@/client"
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
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"
import { AddRequirement } from "./AddRequirement"
import {
  DecisionBadge,
  ReviewBadge,
  TYPES,
  TypeBadge,
  typeLabel,
} from "./badges"
import { RequirementSheet } from "./RequirementSheet"

const select =
  "h-9 rounded-md border border-input bg-transparent px-3 text-sm shadow-xs"
const PROCESSING = ["uploaded", "parsing", "extracting"]

function Documents({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const input = useRef<HTMLInputElement>(null)

  const { data: docs } = useQuery({
    queryKey: ["brd", projectId],
    queryFn: async () =>
      (await BrdService.listBrd({ path: { project_id: projectId } })).data,
    refetchInterval: (q) =>
      q.state.data?.some((d) => PROCESSING.includes(d.status)) ? 2000 : false,
  })
  // When a document finishes processing, its requirements appear: refetch them without a reload.
  const seen = useRef<Record<string, string>>({})
  useEffect(() => {
    let finished = false
    for (const d of docs ?? []) {
      if (
        seen.current[d.id] &&
        seen.current[d.id] !== d.status &&
        !PROCESSING.includes(d.status)
      ) {
        finished = true
      }
      seen.current[d.id] = d.status
    }
    if (finished) {
      queryClient.invalidateQueries({ queryKey: ["requirements", projectId] })
    }
  }, [docs, projectId, queryClient])

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["brd", projectId] })
    queryClient.invalidateQueries({ queryKey: ["requirements", projectId] })
  }

  const upload = useMutation({
    mutationFn: (file: File) =>
      BrdService.uploadBrd({ path: { project_id: projectId }, body: { file } }),
    onSuccess: () => {
      showSuccessToast("Uploaded. Requirements are being extracted.")
      refresh()
    },
    onError: handleError.bind(showErrorToast),
  })
  const reextract = useMutation({
    mutationFn: (id: string) =>
      BrdService.reextract({ path: { document_id: id } }),
    onSuccess: refresh,
    onError: handleError.bind(showErrorToast),
  })
  const remove = useMutation({
    mutationFn: (id: string) =>
      BrdService.deleteBrd({ path: { document_id: id } }),
    onSuccess: refresh,
    onError: handleError.bind(showErrorToast),
  })

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          Upload the BRD (PDF, Word, Markdown or text). Requirements are
          extracted with a quote from the document, so each one can be checked
          against its source.
        </p>
        <div>
          <input
            ref={input}
            type="file"
            accept=".pdf,.docx,.md,.markdown,.txt"
            className="hidden"
            aria-label="BRD file"
            onChange={(e) => {
              const f = e.target.files?.[0]
              if (f) upload.mutate(f)
              e.target.value = ""
            }}
          />
          <Button
            onClick={() => input.current?.click()}
            disabled={upload.isPending}
          >
            <FileUp className="mr-2 h-4 w-4" />
            {upload.isPending ? "Uploading…" : "Upload BRD"}
          </Button>
        </div>
      </div>
      {docs && docs.length > 0 && (
        <ul className="divide-y rounded-md border">
          {docs.map((d) => (
            <li
              key={d.id}
              className="flex flex-wrap items-center justify-between gap-2 p-3 text-sm"
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="truncate font-medium">{d.filename}</span>
                  <StatusBadge
                    status={
                      d.status === "ready"
                        ? "ready"
                        : d.status === "failed"
                          ? "failed"
                          : "running"
                    }
                  />
                  {PROCESSING.includes(d.status) && (
                    <span className="text-xs text-muted-foreground">
                      {d.status}…
                    </span>
                  )}
                </div>
                <p className="text-xs text-muted-foreground">
                  {d.page_count ? `${d.page_count} pages · ` : ""}
                  {d.chunk_count} sections · {d.requirement_count} requirements
                </p>
                {d.error && (
                  <p className="text-xs text-red-600 dark:text-red-400">
                    {d.error}
                  </p>
                )}
              </div>
              <div className="flex items-center gap-1">
                {d.run_id && (
                  <Button asChild size="sm" variant="ghost">
                    <Link to="/runs/$runId" params={{ runId: d.run_id }}>
                      Run
                    </Link>
                  </Button>
                )}
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={
                    PROCESSING.includes(d.status) || reextract.isPending
                  }
                  onClick={() => reextract.mutate(d.id)}
                  title="Extract again. Reviewed requirements are kept."
                >
                  <RefreshCw className="mr-1 h-4 w-4" /> Re-extract
                </Button>
                <Button
                  size="icon"
                  variant="ghost"
                  aria-label={`Delete ${d.filename}`}
                  onClick={() => remove.mutate(d.id)}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export function RequirementsPanel({ projectId }: { projectId: string }) {
  const [status, setStatus] = useState("")
  const [type, setType] = useState("")
  const [onlyDecision, setOnlyDecision] = useState(false)
  const [q, setQ] = useState("")
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [open, setOpen] = useState<string | null>(null)
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const { data: page } = useQuery({
    queryKey: ["requirements", projectId, status, type, onlyDecision, q],
    queryFn: async () =>
      (
        await RequirementsService.listRequirements({
          path: { project_id: projectId },
          query: {
            status: status || undefined,
            type: type || undefined,
            needs_decision: onlyDecision ? true : undefined,
            q: q || undefined,
            limit: 500,
          },
        })
      ).data,
  })
  const { data: summary } = useQuery({
    queryKey: ["requirements", projectId, "summary"],
    queryFn: async () =>
      (
        await RequirementsService.requirementsSummary({
          path: { project_id: projectId },
        })
      ).data,
  })

  const bulk = useMutation({
    mutationFn: (action: "approve" | "reject") =>
      RequirementsService.bulkReview({
        path: { project_id: projectId },
        body: { ids: [...picked], action },
      }),
    onSuccess: ({ data }) => {
      showSuccessToast(
        `${data.updated} updated${data.skipped.length ? `; ${data.skipped.length} skipped because they need a decision first` : ""}`,
      )
      setPicked(new Set())
      queryClient.invalidateQueries({ queryKey: ["requirements", projectId] })
    },
    onError: handleError.bind(showErrorToast),
  })

  const rows = page?.data ?? []
  const allPicked = rows.length > 0 && rows.every((r) => picked.has(r.id))

  return (
    <div className="space-y-6">
      <Documents projectId={projectId} />

      {summary && summary.total > 0 && (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Badge variant="secondary">{summary.total} requirements</Badge>
          <Badge variant="outline" className="border-emerald-500/40">
            {summary.approved} approved
          </Badge>
          {summary.needs_decision > 0 && (
            <button
              type="button"
              onClick={() => setOnlyDecision(!onlyDecision)}
              aria-pressed={onlyDecision}
            >
              <DecisionBadge />
              <span className="ml-1 text-xs underline">
                {summary.needs_decision} to decide
                {onlyDecision ? " (showing only these)" : ""}
              </span>
            </button>
          )}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <Input
          className="w-56"
          placeholder="Search requirements…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <select
          aria-label="Review status"
          className={select}
          value={status}
          onChange={(e) => setStatus(e.target.value)}
        >
          <option value="">any status</option>
          <option value="extracted">to review</option>
          <option value="edited">edited</option>
          <option value="approved">approved</option>
          <option value="rejected">rejected</option>
        </select>
        <select
          aria-label="Requirement type"
          className={select}
          value={type}
          onChange={(e) => setType(e.target.value)}
        >
          <option value="">any type</option>
          {TYPES.map((t) => (
            <option key={t} value={t}>
              {typeLabel(t)}
            </option>
          ))}
        </select>
        <div className="ml-auto flex items-center gap-2">
          {picked.size > 0 && (
            <>
              <Button
                size="sm"
                onClick={() => bulk.mutate("approve")}
                disabled={bulk.isPending}
              >
                <CheckCheck className="mr-1 h-4 w-4" /> Approve {picked.size}
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => bulk.mutate("reject")}
                disabled={bulk.isPending}
              >
                Reject {picked.size}
              </Button>
            </>
          )}
          <AddRequirement projectId={projectId} />
        </div>
      </div>

      {rows.length === 0 ? (
        <div className="rounded-md border p-8 text-center text-muted-foreground">
          {summary && summary.total > 0
            ? "No requirements match these filters."
            : "No requirements yet. Upload a BRD above."}
        </div>
      ) : (
        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-8">
                  <input
                    type="checkbox"
                    aria-label="Select all"
                    checked={allPicked}
                    onChange={(e) =>
                      setPicked(
                        e.target.checked
                          ? new Set(rows.map((r) => r.id))
                          : new Set(),
                      )
                    }
                  />
                </TableHead>
                <TableHead>ID</TableHead>
                <TableHead>Requirement</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Module</TableHead>
                <TableHead>Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((r) => (
                <TableRow
                  key={r.id}
                  className="cursor-pointer"
                  onClick={() => setOpen(r.id)}
                >
                  <TableCell onClick={(e) => e.stopPropagation()}>
                    <input
                      type="checkbox"
                      aria-label={`Select ${r.req_key}`}
                      checked={picked.has(r.id)}
                      onChange={(e) => {
                        const next = new Set(picked)
                        if (e.target.checked) next.add(r.id)
                        else next.delete(r.id)
                        setPicked(next)
                      }}
                    />
                  </TableCell>
                  <TableCell className="whitespace-nowrap font-mono text-xs">
                    {r.req_key}
                  </TableCell>
                  <TableCell className="max-w-md">
                    <button
                      type="button"
                      className="text-left font-medium underline-offset-2 hover:underline"
                      onClick={() => setOpen(r.id)}
                    >
                      {r.title}
                    </button>
                    <p className="truncate text-xs text-muted-foreground">
                      {(r.source_anchor as { section?: string }).section}
                    </p>
                  </TableCell>
                  <TableCell>
                    <TypeBadge type={r.type} />
                  </TableCell>
                  <TableCell>{r.module ?? "—"}</TableCell>
                  <TableCell>
                    <div className="flex flex-wrap items-center gap-1">
                      <ReviewBadge status={r.review_status} />
                      {r.needs_decision && <DecisionBadge />}
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      <RequirementSheet requirementId={open} onClose={() => setOpen(null)} />
    </div>
  )
}
