import { useQuery } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import {
  ArrowRight,
  CheckCircle2,
  Circle,
  FileText,
  FlaskConical,
  FolderKanban,
  Loader2,
  Plus,
  ShieldCheck,
} from "lucide-react"

import { DashboardService } from "@/client"
import { StatusBadge } from "@/components/Runs/StatusBadge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import useAuth from "@/hooks/useAuth"

export const Route = createFileRoute("/_layout/")({
  component: Dashboard,
  head: () => ({ meta: [{ title: "Dashboard - QA Agent" }] }),
})

const STEPS = [
  { key: "roles", label: "Roles", tab: "roles" },
  { key: "requirements", label: "BRD", tab: "requirements" },
  { key: "crawl", label: "Crawl", tab: "crawl" },
  { key: "mapping", label: "Map", tab: "mapping" },
  { key: "tests", label: "Generate", tab: "tests" },
  { key: "review", label: "Review", tab: "tests" },
] as const

const NEXT_TEXT: Record<string, string> = {
  roles: "Add a role with test credentials",
  requirements: "Upload the BRD",
  crawl: "Crawl the application",
  mapping: "Map requirements to screens",
  tests: "Generate test cases",
  review: "Review and approve test cases",
  done: "All cases approved - export them",
}

const KIND_LABEL: Record<string, string> = {
  crawl: "Crawl",
  ingest: "BRD ingest",
  map: "Mapping",
  generate: "Test generation",
  regenerate: "Regeneration",
  full: "Full pipeline",
  echo: "Demo",
}

function Stat({
  icon: Icon,
  label,
  value,
  hint,
}: {
  icon: typeof FileText
  label: string
  value: number | string
  hint?: string
}) {
  return (
    <Card>
      <CardContent className="flex items-center gap-4 p-5">
        <span className="flex size-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <Icon className="size-5" />
        </span>
        <div>
          <div className="text-2xl font-semibold tabular-nums">{value}</div>
          <div className="text-xs text-muted-foreground">{label}</div>
          {hint && (
            <div className="text-[11px] text-muted-foreground">{hint}</div>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

function Stepper({ current }: { current: string }) {
  const at =
    current === "done"
      ? STEPS.length
      : STEPS.findIndex((s) => s.key === current)
  return (
    <ol className="flex items-center gap-1 text-[11px]">
      {STEPS.map((s, i) => (
        <li key={s.key} className="flex items-center gap-1">
          {i < at ? (
            <CheckCircle2 className="size-3.5 text-emerald-500" />
          ) : i === at ? (
            <Circle className="size-3.5 fill-primary/30 text-primary" />
          ) : (
            <Circle className="size-3.5 text-muted-foreground/50" />
          )}
          <span
            className={
              i === at ? "font-medium" : i < at ? "" : "text-muted-foreground"
            }
          >
            {s.label}
          </span>
          {i < STEPS.length - 1 && (
            <span className="mx-0.5 h-px w-3 bg-border" />
          )}
        </li>
      ))}
    </ol>
  )
}

const pct = (a: number, b: number) => (b ? Math.round((100 * a) / b) : 0)

function Bar({ value, label }: { value: number; label: string }) {
  return (
    <div>
      <div className="mb-1 flex justify-between text-[11px] text-muted-foreground">
        <span>{label}</span>
        <span className="tabular-nums">{value}%</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-muted">
        <div
          className="h-full rounded-full bg-primary transition-all"
          style={{ width: `${value}%` }}
        />
      </div>
    </div>
  )
}

function Dashboard() {
  const { user } = useAuth()
  const { data, isLoading } = useQuery({
    queryKey: ["dashboard"],
    queryFn: async () => (await DashboardService.dashboard()).data,
    refetchInterval: 5000,
  })

  const name = user?.full_name || user?.email

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="max-w-lg truncate text-2xl font-semibold tracking-tight">
            Hi, {name}
          </h1>
          <p className="text-sm text-muted-foreground">
            From a BRD to grounded test cases: where each project stands.
          </p>
        </div>
        <Button asChild>
          <Link to="/projects">
            <Plus className="mr-1 size-4" /> New project
          </Link>
        </Button>
      </div>

      {isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}

      {data && data.projects.length === 0 && (
        <Card>
          <CardContent className="flex flex-col items-center gap-3 p-10 text-center">
            <FolderKanban className="size-8 text-muted-foreground" />
            <p className="font-medium">No projects yet</p>
            <p className="max-w-md text-sm text-muted-foreground">
              Create a project for the application you want to test, add the
              user roles, upload the BRD and start a crawl.
            </p>
            <Button asChild>
              <Link to="/projects">Create the first project</Link>
            </Button>
          </CardContent>
        </Card>
      )}

      {data && data.projects.length > 0 && (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Stat
              icon={FolderKanban}
              label="Projects"
              value={data.totals.projects}
            />
            <Stat
              icon={FileText}
              label="Requirements"
              value={data.totals.requirements}
            />
            <Stat
              icon={FlaskConical}
              label="Test cases"
              value={data.totals.test_cases}
              hint={`${data.totals.approved} approved`}
            />
            <Stat
              icon={data.running ? Loader2 : ShieldCheck}
              label={data.running ? "Runs in progress" : "Runs in progress"}
              value={data.running}
              hint={data.running ? "agents are working" : "all quiet"}
            />
          </div>

          <div className="grid gap-4 lg:grid-cols-3">
            <div className="space-y-4 lg:col-span-2">
              <h2 className="text-sm font-medium text-muted-foreground">
                Projects
              </h2>
              {data.projects.map((p) => (
                <Card key={p.id}>
                  <CardHeader className="flex-row items-start justify-between gap-3 pb-2">
                    <div className="min-w-0">
                      <CardTitle className="truncate text-base">
                        <Link
                          to="/projects/$projectId"
                          params={{ projectId: p.id }}
                          className="hover:underline"
                        >
                          {p.name}
                        </Link>
                      </CardTitle>
                      <p className="truncate text-xs text-muted-foreground">
                        {p.target_base_url}
                      </p>
                    </div>
                    <Button asChild size="sm" variant="outline">
                      <Link
                        to="/projects/$projectId"
                        params={{ projectId: p.id }}
                        search={{
                          tab:
                            STEPS.find((s) => s.key === p.next_step)?.tab ??
                            "tests",
                        }}
                      >
                        {NEXT_TEXT[p.next_step]}
                        <ArrowRight className="ml-1 size-3.5" />
                      </Link>
                    </Button>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <Stepper current={p.next_step} />
                    <div className="grid gap-4 sm:grid-cols-2">
                      <Bar
                        label={`Requirements mapped to a screen (${p.mapped_requirements}/${p.requirements})`}
                        value={pct(p.mapped_requirements, p.requirements)}
                      />
                      <Bar
                        label={`Requirements with a test case (${p.covered_requirements}/${p.requirements})`}
                        value={pct(p.covered_requirements, p.requirements)}
                      />
                    </div>
                    <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-muted-foreground">
                      <span>{p.roles} roles</span>
                      <span>{p.screens} screens</span>
                      <span>
                        {p.test_cases} test cases ({p.approved_cases} approved)
                      </span>
                      {p.last_run_kind && (
                        <span className="flex items-center gap-1">
                          last run:{" "}
                          {KIND_LABEL[p.last_run_kind] ?? p.last_run_kind}
                          {p.last_run_status && (
                            <StatusBadge status={p.last_run_status} />
                          )}
                        </span>
                      )}
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>

            <div className="space-y-4">
              <h2 className="text-sm font-medium text-muted-foreground">
                Recent runs
              </h2>
              <Card>
                <CardContent className="divide-y p-0">
                  {data.recent_runs.length === 0 && (
                    <p className="p-4 text-sm text-muted-foreground">
                      Nothing has run yet.
                    </p>
                  )}
                  {data.recent_runs.map((r) => (
                    <Link
                      key={r.id}
                      to="/runs/$runId"
                      params={{ runId: r.id }}
                      className="flex items-center justify-between gap-2 p-3 text-sm transition-colors hover:bg-accent"
                    >
                      <span className="min-w-0">
                        <span className="block truncate font-medium">
                          {KIND_LABEL[r.kind] ?? r.kind}
                          {r.partial ? " (partial)" : ""}
                        </span>
                        <span className="block truncate text-xs text-muted-foreground">
                          {r.project_name} ·{" "}
                          {new Date(r.created_at).toLocaleString([], {
                            month: "short",
                            day: "numeric",
                            hour: "2-digit",
                            minute: "2-digit",
                          })}
                          {r.seconds ? ` · ${Math.round(r.seconds)}s` : ""}
                        </span>
                      </span>
                      <StatusBadge status={r.status} />
                    </Link>
                  ))}
                </CardContent>
              </Card>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
