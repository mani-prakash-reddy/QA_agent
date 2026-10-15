import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"

import { type RequirementPublic, RequirementsService } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"
import {
  DecisionBadge,
  ReviewBadge,
  TYPES,
  TypeBadge,
  typeLabel,
} from "./badges"

const field =
  "w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs"

/** The requirement's source text with its verbatim quote highlighted. */
function SourceText({ text, quote }: { text: string; quote: string }) {
  const i = quote ? text.toLowerCase().indexOf(quote.toLowerCase()) : -1
  if (i < 0) return <p className="whitespace-pre-wrap text-sm">{text}</p>
  return (
    <p className="whitespace-pre-wrap text-sm">
      {text.slice(0, i)}
      <mark className="rounded bg-amber-400/30 px-0.5 text-foreground">
        {text.slice(i, i + quote.length)}
      </mark>
      {text.slice(i + quote.length)}
    </p>
  )
}

function EditForm({
  req,
  onDone,
}: {
  req: RequirementPublic
  onDone: () => void
}) {
  const [title, setTitle] = useState(req.title)
  const [description, setDescription] = useState(req.description)
  const [type, setType] = useState(req.type)
  const [module, setModule] = useState(req.module ?? "")
  const [actors, setActors] = useState(req.actors.join(", "))
  const [criteria, setCriteria] = useState(req.acceptance_criteria.join("\n"))
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const save = useMutation({
    mutationFn: () =>
      RequirementsService.updateRequirement({
        path: { requirement_id: req.id },
        body: {
          title,
          description,
          type: type as RequirementPublic["type"] as never,
          module: module || null,
          actors: actors
            .split(",")
            .map((a) => a.trim())
            .filter(Boolean),
          acceptance_criteria: criteria
            .split("\n")
            .map((a) => a.trim())
            .filter(Boolean),
        },
      }),
    onSuccess: ({ data }) => {
      showSuccessToast(
        data.needs_decision
          ? "Saved. The wording is still ambiguous, so it needs a decision."
          : "Saved. Approve it when you are happy with it.",
      )
      queryClient.invalidateQueries({ queryKey: ["requirements"] })
      queryClient.invalidateQueries({ queryKey: ["requirement", req.id] })
      onDone()
    },
    onError: handleError.bind(showErrorToast),
  })
  return (
    <form
      className="grid gap-3"
      onSubmit={(e) => {
        e.preventDefault()
        save.mutate()
      }}
    >
      <div className="grid gap-1">
        <Label htmlFor="e-title">Title</Label>
        <Input
          id="e-title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
      </div>
      <div className="grid gap-1">
        <Label htmlFor="e-desc">Description</Label>
        <textarea
          id="e-desc"
          rows={4}
          className={field}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div className="grid gap-1">
          <Label htmlFor="e-type">Type</Label>
          <select
            id="e-type"
            className={field}
            value={type}
            onChange={(e) => setType(e.target.value)}
          >
            {TYPES.map((t) => (
              <option key={t} value={t}>
                {typeLabel(t)}
              </option>
            ))}
          </select>
        </div>
        <div className="grid gap-1">
          <Label htmlFor="e-mod">Module</Label>
          <Input
            id="e-mod"
            value={module}
            onChange={(e) => setModule(e.target.value)}
          />
        </div>
      </div>
      <div className="grid gap-1">
        <Label htmlFor="e-actors">Roles (comma separated)</Label>
        <Input
          id="e-actors"
          value={actors}
          onChange={(e) => setActors(e.target.value)}
        />
      </div>
      <div className="grid gap-1">
        <Label htmlFor="e-ac">Acceptance criteria (one per line)</Label>
        <textarea
          id="e-ac"
          rows={3}
          className={field}
          value={criteria}
          onChange={(e) => setCriteria(e.target.value)}
        />
      </div>
      <div className="flex gap-2">
        <Button
          type="submit"
          disabled={save.isPending || !title.trim() || !description.trim()}
        >
          Save changes
        </Button>
        <Button type="button" variant="outline" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  )
}

export function RequirementSheet({
  requirementId,
  onClose,
}: {
  requirementId: string | null
  onClose: () => void
}) {
  const [editing, setEditing] = useState(false)
  const queryClient = useQueryClient()
  const { showErrorToast } = useCustomToast()
  const { data: r } = useQuery({
    queryKey: ["requirement", requirementId],
    queryFn: async () =>
      (
        await RequirementsService.readRequirement({
          path: { requirement_id: requirementId as string },
        })
      ).data,
    enabled: !!requirementId,
  })

  const act = useMutation({
    mutationFn: (action: "approve" | "reject" | "accept" | "reset") => {
      const path = { requirement_id: requirementId as string }
      if (action === "approve")
        return RequirementsService.approveRequirement({ path })
      if (action === "reject")
        return RequirementsService.rejectRequirement({ path })
      if (action === "accept")
        return RequirementsService.acceptWording({ path })
      return RequirementsService.resetRequirement({ path })
    },
    onError: handleError.bind(showErrorToast),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["requirements"] })
      queryClient.invalidateQueries({
        queryKey: ["requirement", requirementId],
      })
    },
  })

  const anchor = (r?.source_anchor ?? {}) as {
    section?: string
    page?: number
    quote?: string
  }
  return (
    <Sheet
      open={!!requirementId}
      onOpenChange={(o) => {
        if (!o) {
          setEditing(false)
          onClose()
        }
      }}
    >
      <SheetContent className="w-full overflow-y-auto sm:max-w-2xl">
        <SheetHeader>
          <SheetTitle>
            {r ? `${r.req_key} · ${r.title}` : "Loading…"}
          </SheetTitle>
          <SheetDescription>
            {r ? (
              <span className="flex flex-wrap items-center gap-2">
                <TypeBadge type={r.type} />
                <ReviewBadge status={r.review_status} />
                {r.needs_decision && <DecisionBadge />}
                <span className="text-xs">
                  v{r.version}
                  {r.key_origin === "manual" ? " · added by hand" : ""}
                </span>
              </span>
            ) : null}
          </SheetDescription>
        </SheetHeader>
        {r && (
          <div className="space-y-6 px-4 pb-6">
            {r.ambiguity_reasons.length > 0 && (
              <section className="space-y-2">
                <h3 className="font-semibold">
                  {r.needs_decision ? "Why this needs a decision" : "Notes"}
                </h3>
                <ul className="space-y-1 text-sm">
                  {(
                    r.ambiguity_reasons as {
                      source: string
                      reason: string
                      severity: string
                    }[]
                  ).map((x) => (
                    <li key={x.reason} className="flex items-start gap-2">
                      <Badge
                        variant={
                          x.severity === "blocking" ? "destructive" : "outline"
                        }
                        className="shrink-0"
                      >
                        {x.severity === "blocking" ? "ambiguous" : "note"}
                      </Badge>
                      <span>
                        {x.reason}{" "}
                        <span className="text-xs text-muted-foreground">
                          ({x.source === "llm" ? "AI review" : "wording check"})
                        </span>
                      </span>
                    </li>
                  ))}
                </ul>
                {r.needs_decision && (
                  <p className="text-sm text-muted-foreground">
                    Test cases are not generated from an ambiguous requirement
                    until you rewrite it or accept it as written.
                  </p>
                )}
              </section>
            )}

            {editing ? (
              <EditForm req={r} onDone={() => setEditing(false)} />
            ) : (
              <>
                <section className="space-y-1">
                  <h3 className="font-semibold">Description</h3>
                  <p className="whitespace-pre-wrap text-sm">{r.description}</p>
                </section>
                {r.acceptance_criteria.length > 0 && (
                  <section className="space-y-1">
                    <h3 className="font-semibold">Acceptance criteria</h3>
                    <ul className="list-disc space-y-1 pl-5 text-sm">
                      {r.acceptance_criteria.map((c) => (
                        <li key={String(c)}>{String(c)}</li>
                      ))}
                    </ul>
                  </section>
                )}
                <section className="grid grid-cols-2 gap-3 text-sm">
                  <div>
                    <h3 className="font-semibold">Module</h3>
                    <p className="text-muted-foreground">{r.module ?? "—"}</p>
                  </div>
                  <div>
                    <h3 className="font-semibold">Roles</h3>
                    <p className="text-muted-foreground">
                      {r.actors.length ? r.actors.join(", ") : "—"}
                    </p>
                  </div>
                </section>
              </>
            )}

            <section className="space-y-2">
              <h3 className="font-semibold">Source in the BRD</h3>
              <p className="text-xs text-muted-foreground">
                {anchor.section || "—"}
                {anchor.page ? ` · page ${anchor.page}` : ""}
              </p>
              {r.source_text ? (
                <div className="rounded-md border bg-muted/30 p-3">
                  <SourceText text={r.source_text} quote={anchor.quote ?? ""} />
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">
                  No source text (added by hand).
                </p>
              )}
            </section>

            {!editing && (
              <div className="flex flex-wrap gap-2 border-t pt-4">
                {r.review_status !== "approved" && (
                  <Button
                    onClick={() => act.mutate("approve")}
                    disabled={act.isPending || r.needs_decision}
                    title={
                      r.needs_decision
                        ? "Rewrite it or accept its wording first"
                        : undefined
                    }
                  >
                    Approve
                  </Button>
                )}
                {r.needs_decision && (
                  <Button
                    variant="outline"
                    onClick={() => act.mutate("accept")}
                    disabled={act.isPending}
                  >
                    Accept wording as written
                  </Button>
                )}
                <Button variant="outline" onClick={() => setEditing(true)}>
                  Edit
                </Button>
                {r.review_status !== "rejected" ? (
                  <Button
                    variant="outline"
                    onClick={() => act.mutate("reject")}
                    disabled={act.isPending}
                  >
                    Reject
                  </Button>
                ) : null}
                {r.review_status !== "extracted" && (
                  <Button
                    variant="ghost"
                    onClick={() => act.mutate("reset")}
                    disabled={act.isPending}
                  >
                    Back to review
                  </Button>
                )}
              </div>
            )}
          </div>
        )}
      </SheetContent>
    </Sheet>
  )
}
