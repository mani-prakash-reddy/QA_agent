import { useMutation, useQuery } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { ExternalLink, UploadCloud } from "lucide-react"
import { useEffect, useState } from "react"

import { IntegrationsService, type PushResult } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { LoadingButton } from "@/components/ui/loading-button"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

const NAME: Record<string, string> = {
  jira: "Jira",
  testrail: "TestRail",
  zephyr: "Zephyr Scale",
}
const select =
  "h-9 w-full rounded-md border bg-background px-2 text-sm disabled:opacity-50"

function Pick({
  label,
  platform,
  kind,
  parent,
  value,
  onChange,
  optional,
}: {
  label: string
  platform: string
  kind: string
  parent: string
  value: string
  onChange: (v: string) => void
  optional?: boolean
}) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["int-options", platform, kind, parent],
    queryFn: async () =>
      (
        await IntegrationsService.integrationOptions({
          path: { platform: platform as "jira" },
          query: { kind, parent },
        })
      ).data,
    enabled: parent !== "",
    retry: false,
  })
  return (
    <div className="grid gap-1.5">
      <Label>{label}</Label>
      <select
        aria-label={label}
        className={select}
        value={value}
        disabled={parent === "" || isLoading}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">
          {isLoading ? "Loading…" : optional ? "(none)" : "Choose…"}
        </option>
        {(data ?? []).map((o) => (
          <option key={o.id} value={o.id}>
            {o.name}
          </option>
        ))}
      </select>
      {error && (
        <p className="text-xs text-red-600">
          Could not load the list. Check the connection under Integrations.
        </p>
      )}
    </div>
  )
}

/** Push this project's approved test cases to the user's own Jira, TestRail or Zephyr account. */
export function PushDialog({ projectId }: { projectId: string }) {
  const { showErrorToast } = useCustomToast()
  const [open, setOpen] = useState(false)
  const [platform, setPlatform] = useState("")
  const [a, setA] = useState("") // project
  const [b, setB] = useState("") // suite / issue type / folder
  const [c, setC] = useState("") // section
  const [drafts, setDrafts] = useState(false)
  const [result, setResult] = useState<PushResult | null>(null)

  const { data: integrations } = useQuery({
    queryKey: ["integrations"],
    queryFn: async () => (await IntegrationsService.listIntegrations()).data,
    enabled: open,
  })
  const connected = (integrations ?? []).filter((i) => i.connected)

  useEffect(() => {
    if (!platform && connected.length) setPlatform(connected[0].platform)
  }, [platform, connected])
  const choosePlatform = (v: string) => {
    setPlatform(v)
    setA("")
    setB("")
    setC("")
    setResult(null)
  }
  const chooseProject = (v: string) => {
    setA(v)
    setB("")
    setC("")
    setResult(null)
  }

  const destination = () => {
    if (platform === "jira") return { project_key: a, issue_type: b || "Task" }
    if (platform === "testrail")
      return {
        project_id: Number(a),
        suite_id: b ? Number(b) : null,
        section_id: Number(c),
      }
    return { project_key: a, folder_id: b || null }
  }
  const complete =
    platform === "testrail" ? a !== "" && c !== "" : a !== "" && platform !== ""

  const run = useMutation({
    mutationFn: (dry: boolean) =>
      IntegrationsService.push({
        path: { project_id: projectId, platform: platform as "jira" },
        body: {
          destination: destination(),
          dry_run: dry,
          include_drafts: drafts,
        },
      }),
    onSuccess: ({ data }) => setResult(data),
    onError: handleError.bind(showErrorToast),
  })

  const counts = result
    ? Object.entries(result.counts)
        .map(([k, v]) => `${v} ${k}`)
        .join(" · ")
    : ""

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline">
          <UploadCloud className="mr-2 h-4 w-4" /> Push to…
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Push test cases</DialogTitle>
          <DialogDescription>
            Approved cases go to your connected account. Cases pushed before are
            updated, never duplicated.
          </DialogDescription>
        </DialogHeader>

        {integrations && connected.length === 0 ? (
          <p className="rounded-md border p-4 text-sm">
            Nothing is connected yet.{" "}
            <Link to="/integrations" className="underline">
              Add your Jira, TestRail or Zephyr key under Integrations
            </Link>
            .
          </p>
        ) : (
          <div className="grid gap-3">
            <div className="grid gap-1.5">
              <Label>Platform</Label>
              <select
                aria-label="Platform"
                className={select}
                value={platform}
                onChange={(e) => choosePlatform(e.target.value)}
              >
                {connected.map((i) => (
                  <option key={i.platform} value={i.platform}>
                    {NAME[i.platform]}
                  </option>
                ))}
              </select>
            </div>
            {platform && (
              <>
                <Pick
                  label="Project"
                  platform={platform}
                  kind="projects"
                  parent="-"
                  value={a}
                  onChange={chooseProject}
                />
                {platform === "jira" && (
                  <Pick
                    label="Issue type"
                    platform={platform}
                    kind="issue_types"
                    parent={a}
                    value={b}
                    onChange={setB}
                    optional
                  />
                )}
                {platform === "testrail" && (
                  <>
                    <Pick
                      label="Suite (if the project uses suites)"
                      platform={platform}
                      kind="suites"
                      parent={a}
                      value={b}
                      onChange={setB}
                      optional
                    />
                    <Pick
                      label="Section"
                      platform={platform}
                      kind="sections"
                      parent={a ? `${a}:${b}` : ""}
                      value={c}
                      onChange={setC}
                    />
                  </>
                )}
                {platform === "zephyr" && (
                  <Pick
                    label="Folder"
                    platform={platform}
                    kind="folders"
                    parent={a}
                    value={b}
                    onChange={setB}
                    optional
                  />
                )}
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={drafts}
                    onChange={(e) => setDrafts(e.target.checked)}
                  />
                  Include cases that are not approved yet
                </label>
                <div className="flex gap-2">
                  <LoadingButton
                    variant="outline"
                    loading={run.isPending && run.variables === true}
                    disabled={!complete || run.isPending}
                    onClick={() => run.mutate(true)}
                  >
                    Preview
                  </LoadingButton>
                  <LoadingButton
                    loading={run.isPending && run.variables === false}
                    disabled={!complete || run.isPending}
                    onClick={() => run.mutate(false)}
                  >
                    Push now
                  </LoadingButton>
                </div>
              </>
            )}

            {result && (
              <div className="space-y-2 rounded-md border p-3 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant={result.dry_run ? "secondary" : "default"}>
                    {result.dry_run ? "Preview" : "Pushed"}
                  </Badge>
                  <span>{counts || "no approved cases to push"}</span>
                </div>
                <ul className="max-h-48 divide-y overflow-y-auto">
                  {result.cases.map((r) => (
                    <li
                      key={r.tc_key}
                      className="flex items-center justify-between gap-2 py-1"
                    >
                      <span className="min-w-0 truncate">
                        <span className="font-mono text-xs">{r.tc_key}</span>{" "}
                        {r.title}
                      </span>
                      <span className="flex shrink-0 items-center gap-2 text-xs">
                        <span
                          className={
                            r.action === "failed"
                              ? "text-red-600"
                              : "text-muted-foreground"
                          }
                          title={r.error ?? undefined}
                        >
                          {r.action}
                        </span>
                        {r.url && (
                          <a
                            href={r.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            aria-label={`Open ${r.tc_key}`}
                          >
                            <ExternalLink className="size-3" />
                          </a>
                        )}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
