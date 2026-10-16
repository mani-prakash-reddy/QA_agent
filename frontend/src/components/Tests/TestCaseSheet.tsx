import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { History, Pencil, RefreshCw } from "lucide-react"
import { useEffect, useState } from "react"

import { type TestCasePublic, TestsService } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"
import { PriorityBadge, StatusBadge, TEST_TYPES, typeName } from "./TestsPanel"

const BASIS: Record<string, string> = {
  observed: "Taken from what the app showed during the crawl.",
  inferred:
    "The app did not show this result (the crawler never submits forms); it is the expected behaviour for the recorded constraint. Check it.",
  requirement:
    "Follows from the requirement text, not from an observed result.",
  not_observed:
    "Another role reached this screen during the crawl and this role did not. The denial was not probed: confirm the intended permission.",
}

const box =
  "w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs"

function EditForm({
  t,
  projectId,
  onDone,
}: {
  t: TestCasePublic
  projectId: string
  onDone: () => void
}) {
  const queryClient = useQueryClient()
  const { showErrorToast, showSuccessToast } = useCustomToast()
  const [title, setTitle] = useState(t.title)
  const [pre, setPre] = useState(t.preconditions.map(String).join("\n"))
  const [steps, setSteps] = useState(t.rendered_steps.map(String).join("\n"))
  const [expected, setExpected] = useState(t.expected_result)
  const [priority, setPriority] = useState(t.priority)
  const [type, setType] = useState(t.test_type)
  const [note, setNote] = useState("")
  const lines = (x: string) =>
    x
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean)
  const save = useMutation({
    mutationFn: () =>
      TestsService.editTest({
        path: { test_id: t.id },
        body: {
          title,
          preconditions: lines(pre),
          rendered_steps: lines(steps),
          expected_result: expected,
          priority: priority as "P1",
          test_type: type as "positive",
          note,
        },
      }),
    onSuccess: () => {
      showSuccessToast("Saved as a new version. Approve it again when ready.")
      queryClient.invalidateQueries({ queryKey: ["test", t.id] })
      queryClient.invalidateQueries({ queryKey: ["tests", projectId] })
      queryClient.invalidateQueries({ queryKey: ["rtm"] })
      onDone()
    },
    onError: handleError.bind(showErrorToast),
  })
  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <span className="font-medium">Title</span>
        <Input
          aria-label="Title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
      </div>
      <label className="block space-y-1">
        <span className="font-medium">Preconditions (one per line)</span>
        <textarea
          className={box}
          rows={3}
          value={pre}
          onChange={(e) => setPre(e.target.value)}
        />
      </label>
      <label className="block space-y-1">
        <span className="font-medium">Steps (one per line)</span>
        <textarea
          className={box}
          rows={7}
          value={steps}
          onChange={(e) => setSteps(e.target.value)}
        />
        <span className="text-xs text-muted-foreground">
          Editing the text marks the case as hand-edited: its structured steps
          are kept as generated but no longer drive the wording.
        </span>
      </label>
      <label className="block space-y-1">
        <span className="font-medium">Expected result</span>
        <textarea
          className={box}
          rows={3}
          value={expected}
          onChange={(e) => setExpected(e.target.value)}
        />
      </label>
      <div className="flex gap-3">
        <label className="space-y-1">
          <span className="font-medium">Priority</span>
          <select
            aria-label="Priority"
            className={box}
            value={priority}
            onChange={(e) => setPriority(e.target.value)}
          >
            {["P1", "P2", "P3", "P4"].map((p) => (
              <option key={p}>{p}</option>
            ))}
          </select>
        </label>
        <label className="space-y-1">
          <span className="font-medium">Type</span>
          <select
            aria-label="Type"
            className={box}
            value={type}
            onChange={(e) => setType(e.target.value)}
          >
            {TEST_TYPES.map((p) => (
              <option key={p} value={p}>
                {typeName(p)}
              </option>
            ))}
          </select>
        </label>
      </div>
      <Input
        aria-label="Change note"
        placeholder="What did you change and why? (optional)"
        value={note}
        onChange={(e) => setNote(e.target.value)}
      />
      <div className="flex gap-2">
        <Button
          disabled={!title.trim() || save.isPending}
          onClick={() => save.mutate()}
        >
          Save changes
        </Button>
        <Button variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </div>
  )
}

function VersionList({
  t,
  projectId,
}: {
  t: TestCasePublic
  projectId: string
}) {
  const queryClient = useQueryClient()
  const { showErrorToast } = useCustomToast()
  const { data } = useQuery({
    queryKey: ["test", t.id, "versions", t.version],
    queryFn: async () =>
      (await TestsService.testVersions({ path: { test_id: t.id } })).data,
  })
  const restore = useMutation({
    mutationFn: (version: number) =>
      TestsService.restoreTest({ path: { test_id: t.id }, body: { version } }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["test", t.id] })
      queryClient.invalidateQueries({ queryKey: ["tests", projectId] })
    },
    onError: handleError.bind(showErrorToast),
  })
  return (
    <ul className="space-y-2">
      {(data ?? []).map((v) => (
        <li key={v.version} className="rounded-md border p-2">
          <div className="flex items-center justify-between gap-2">
            <span>
              <strong>v{v.version}</strong> · {v.change_type} ·{" "}
              {new Date(v.created_at).toLocaleString()}
            </span>
            {v.version !== t.version && (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => restore.mutate(v.version)}
              >
                Restore
              </Button>
            )}
          </div>
          {v.change_note && (
            <p className="text-xs text-muted-foreground">{v.change_note}</p>
          )}
          <p className="text-xs">{String(v.content.title ?? "")}</p>
        </li>
      ))}
    </ul>
  )
}

export function TestCaseSheet({
  testId,
  projectId,
  onClose,
}: {
  testId: string | null
  projectId: string
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { showErrorToast } = useCustomToast()
  const [mode, setMode] = useState<"view" | "edit" | "history" | "regen">(
    "view",
  )
  const [feedback, setFeedback] = useState("")
  // biome-ignore lint/correctness/useExhaustiveDependencies: reset the drawer whenever another case opens
  useEffect(() => {
    setMode("view")
    setFeedback("")
  }, [testId])

  const { data: t } = useQuery({
    queryKey: ["test", testId],
    queryFn: async () =>
      (await TestsService.readTest({ path: { test_id: testId as string } }))
        .data,
    enabled: !!testId,
  })
  const setStatus = useMutation({
    mutationFn: (status: "draft" | "approved" | "rejected") =>
      TestsService.setStatus({
        path: { test_id: testId as string },
        body: { status },
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["test", testId] })
      queryClient.invalidateQueries({ queryKey: ["tests", projectId] })
      queryClient.invalidateQueries({ queryKey: ["rtm", projectId] })
    },
    onError: handleError.bind(showErrorToast),
  })
  const regen = useMutation({
    mutationFn: () =>
      TestsService.regenerate({
        path: { test_id: testId as string },
        body: { feedback },
      }),
    onSuccess: ({ data }) => {
      queryClient.invalidateQueries({ queryKey: ["runs", projectId] })
      navigate({ to: "/runs/$runId", params: { runId: data.id } })
    },
    onError: handleError.bind(showErrorToast),
  })
  const basis = String(t?.evidence?.basis ?? "")

  return (
    <Sheet open={!!testId} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-2xl">
        <SheetHeader>
          <SheetTitle>{t ? `${t.tc_key} · ${t.title}` : "Loading…"}</SheetTitle>
          <SheetDescription>
            {t ? `${t.req_key} · ${t.requirement_title}` : ""}
          </SheetDescription>
        </SheetHeader>
        {t && (
          <div className="space-y-5 px-4 pb-6 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="secondary">{typeName(t.test_type)}</Badge>
              <PriorityBadge p={t.priority} />
              <StatusBadge s={t.status} />
              <Badge variant="outline">
                {t.origin === "llm" ? "scenario (model-written)" : "rule-based"}
              </Badge>
              {t.role_name && <Badge variant="outline">{t.role_name}</Badge>}
              {t.module && <Badge variant="outline">{t.module}</Badge>}
              {t.version > 1 && <Badge variant="outline">v{t.version}</Badge>}
              {t.also_covers.length > 0 && (
                <Badge variant="outline">
                  also covers {t.also_covers.join(", ")}
                </Badge>
              )}
            </div>

            {t.evidence?.requirement_changed === true && (
              <div
                role="status"
                className="rounded-md border border-amber-500/40 bg-amber-500/10 p-2"
              >
                The requirement's wording changed after this case was written.
                {t.status === "needs_regen"
                  ? " It is queued for regeneration."
                  : " It keeps your decision; check it still matches."}
              </div>
            )}
            {t.evidence?.hand_edited === true && (
              <p className="text-xs text-muted-foreground">
                Steps were edited by hand.
              </p>
            )}

            {mode === "edit" ? (
              <EditForm
                t={t}
                projectId={projectId}
                onDone={() => setMode("view")}
              />
            ) : (
              <>
                <section>
                  <h3 className="mb-1 font-semibold">Preconditions</h3>
                  <ul className="list-disc space-y-1 pl-5">
                    {t.preconditions.map((p) => (
                      <li key={String(p)}>{String(p)}</li>
                    ))}
                  </ul>
                </section>
                <section>
                  <h3 className="mb-1 font-semibold">Steps</h3>
                  <ol className="space-y-1">
                    {t.rendered_steps.map((s) => (
                      <li key={String(s)}>{String(s)}</li>
                    ))}
                  </ol>
                </section>
                {t.test_data.length > 0 && (
                  <section>
                    <h3 className="mb-1 font-semibold">Test data</h3>
                    <table className="w-full text-left">
                      <tbody>
                        {(
                          t.test_data as { field: string; value: string }[]
                        ).map((d, i) => (
                          <tr key={`${d.field}-${i}`} className="border-b">
                            <td className="py-1 pr-3 font-medium">{d.field}</td>
                            <td className="py-1 font-mono text-xs">
                              {d.value}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </section>
                )}
                <section>
                  <h3 className="mb-1 font-semibold">Expected result</h3>
                  <p>{t.expected_result}</p>
                  {BASIS[basis] && (
                    <p
                      className={`mt-1 text-xs ${basis === "observed" ? "text-muted-foreground" : "text-amber-700 dark:text-amber-400"}`}
                    >
                      {BASIS[basis]}
                    </p>
                  )}
                </section>
              </>
            )}

            {mode === "history" && (
              <section>
                <h3 className="mb-1 font-semibold">Version history</h3>
                <VersionList t={t} projectId={projectId} />
              </section>
            )}

            {mode === "regen" && (
              <section className="space-y-2">
                <h3 className="font-semibold">
                  Regenerate {t.req_key}'s unreviewed cases
                </h3>
                <p className="text-xs text-muted-foreground">
                  Drafts for this requirement are written again. Approved and
                  hand-edited cases are kept.
                </p>
                <textarea
                  aria-label="Feedback"
                  className={box}
                  rows={3}
                  placeholder="What should be different? e.g. cover the empty-list case"
                  value={feedback}
                  onChange={(e) => setFeedback(e.target.value)}
                />
                <div className="flex gap-2">
                  <Button
                    disabled={regen.isPending}
                    onClick={() => regen.mutate()}
                  >
                    Regenerate
                  </Button>
                  <Button variant="ghost" onClick={() => setMode("view")}>
                    Cancel
                  </Button>
                </div>
              </section>
            )}

            {mode !== "edit" && (
              <div className="flex flex-wrap gap-2 border-t pt-4">
                <Button
                  disabled={t.status === "approved" || setStatus.isPending}
                  onClick={() => setStatus.mutate("approved")}
                >
                  Approve
                </Button>
                <Button
                  variant="outline"
                  disabled={t.status === "rejected" || setStatus.isPending}
                  onClick={() => setStatus.mutate("rejected")}
                >
                  Reject
                </Button>
                {t.status !== "draft" && (
                  <Button
                    variant="ghost"
                    onClick={() => setStatus.mutate("draft")}
                  >
                    Back to draft
                  </Button>
                )}
                <Button variant="ghost" onClick={() => setMode("edit")}>
                  <Pencil className="mr-1 h-4 w-4" /> Edit
                </Button>
                <Button variant="ghost" onClick={() => setMode("history")}>
                  <History className="mr-1 h-4 w-4" /> History
                </Button>
                <Button variant="ghost" onClick={() => setMode("regen")}>
                  <RefreshCw className="mr-1 h-4 w-4" /> Regenerate…
                </Button>
              </div>
            )}
          </div>
        )}
      </SheetContent>
    </Sheet>
  )
}
