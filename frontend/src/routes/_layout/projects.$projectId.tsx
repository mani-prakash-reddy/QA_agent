import { useQuery } from "@tanstack/react-query"
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router"
import {
  ArrowRight,
  BarChart3,
  CheckCircle2,
  Circle,
  ExternalLink,
} from "lucide-react"

import { DashboardService, ProjectsService } from "@/client"
import { MappingPanel } from "@/components/Mapping/MappingPanel"
import { CrawlLogPanel } from "@/components/Projects/CrawlLogPanel"
import { MetricsPanel } from "@/components/Projects/MetricsPanel"
import { ModelPanel } from "@/components/Projects/ModelPanel"
import { RolesPanel } from "@/components/Projects/RolesPanel"
import { RunsPanel } from "@/components/Projects/RunsPanel"
import { ReferencePanel } from "@/components/Requirements/ReferencePanel"
import { RequirementsPanel } from "@/components/Requirements/RequirementsPanel"
import { TestsPanel } from "@/components/Tests/TestsPanel"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

type Search = { tab?: string; snapshot?: string }

export const Route = createFileRoute("/_layout/projects/$projectId")({
  component: ProjectPage,
  validateSearch: (s: Record<string, unknown>): Search => ({
    tab: typeof s.tab === "string" ? s.tab : undefined,
    snapshot: typeof s.snapshot === "string" ? s.snapshot : undefined,
  }),
  head: () => ({ meta: [{ title: "Project - QA Agent" }] }),
})

type Step = {
  key: string
  label: string
  hint: string
  tabs: { value: string; label: string }[]
}

// The work flows left to right; the second row holds the views that belong to the chosen step.
const STEPS: Step[] = [
  {
    key: "roles",
    label: "Roles",
    hint: "Who signs in",
    tabs: [{ value: "roles", label: "Roles" }],
  },
  {
    key: "requirements",
    label: "Requirements",
    hint: "The BRD",
    tabs: [
      { value: "requirements", label: "Requirements" },
      { value: "reference", label: "Reference documents" },
    ],
  },
  {
    key: "crawl",
    label: "Explore",
    hint: "Crawl the app",
    tabs: [
      { value: "crawl", label: "Runs" },
      { value: "model", label: "Screens" },
      { value: "matrix", label: "Permissions" },
      { value: "log", label: "Crawl log" },
    ],
  },
  {
    key: "mapping",
    label: "Mapping",
    hint: "Requirement ↔ screen",
    tabs: [{ value: "mapping", label: "Mapping and gaps" }],
  },
  {
    key: "tests",
    label: "Test cases",
    hint: "Generate and review",
    tabs: [{ value: "tests", label: "Test cases" }],
  },
]

const stepOf = (tab: string) =>
  STEPS.findIndex((s) => s.tabs.some((t) => t.value === tab))

const NEXT_TEXT: Record<string, string> = {
  roles: "Add a role with test credentials",
  requirements: "Upload the BRD",
  crawl: "Crawl the application",
  mapping: "Map requirements to screens",
  tests: "Generate test cases",
  review: "Review and approve the test cases",
  done: "Everything is approved - export the cases",
}
const NEXT_TAB: Record<string, string> = {
  roles: "roles",
  requirements: "requirements",
  crawl: "crawl",
  mapping: "mapping",
  tests: "tests",
  review: "tests",
  done: "tests",
}

function ProjectPage() {
  const { projectId } = Route.useParams()
  const { tab = "roles", snapshot } = Route.useSearch()
  const navigate = useNavigate({ from: Route.fullPath })
  const { data: project } = useQuery({
    queryKey: ["project", projectId],
    queryFn: async () =>
      (await ProjectsService.readProject({ path: { project_id: projectId } }))
        .data,
  })
  const { data: dash } = useQuery({
    queryKey: ["dashboard"],
    queryFn: async () => (await DashboardService.dashboard()).data,
    refetchInterval: 8000,
  })
  if (!project) return <p className="text-muted-foreground">Loading…</p>

  const prog = dash?.projects.find((p) => p.id === projectId)
  const go = (t: string) =>
    navigate({ search: (prev: Search) => ({ ...prev, tab: t }) })
  const current = Math.max(0, stepOf(tab))
  const done = [
    (prog?.roles ?? 0) > 0,
    (prog?.requirements ?? 0) > 0,
    (prog?.screens ?? 0) > 0,
    (prog?.mapped_requirements ?? 0) > 0,
    (prog?.test_cases ?? 0) > 0,
  ]
  const counts = [
    prog ? String(prog.roles) : "",
    prog ? String(prog.requirements) : "",
    prog ? `${prog.screens} screens` : "",
    prog ? `${prog.mapped_requirements}/${prog.requirements}` : "",
    prog ? `${prog.test_cases}` : "",
  ]
  const onSnapshot = (id: string) =>
    navigate({ search: (prev: Search) => ({ ...prev, snapshot: id }) })

  return (
    <div className="flex min-w-0 flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm text-muted-foreground">
            <Link to="/projects" className="underline">
              Projects
            </Link>{" "}
            /
          </p>
          <div className="flex items-center gap-3">
            <h1 className="truncate text-2xl font-semibold tracking-tight">
              {project.name}
            </h1>
            {project.confidential && (
              <Badge variant="outline">confidential</Badge>
            )}
            <Badge variant="secondary">staging confirmed</Badge>
          </div>
          <a
            href={project.target_base_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:underline"
          >
            {project.target_base_url}
            <ExternalLink className="size-3" />
          </a>
        </div>
        {prog && prog.next_step !== "done" && (
          <div className="flex items-center gap-3 rounded-lg border bg-card px-3 py-2">
            <div className="text-sm">
              <div className="text-[11px] uppercase tracking-wide text-muted-foreground">
                Next step
              </div>
              <div className="font-medium">{NEXT_TEXT[prog.next_step]}</div>
            </div>
            <Button size="sm" onClick={() => go(NEXT_TAB[prog.next_step])}>
              Go <ArrowRight className="ml-1 size-3.5" />
            </Button>
          </div>
        )}
      </div>

      <nav aria-label="Workflow" className="overflow-x-auto">
        <ol className="flex min-w-max items-stretch gap-2">
          {STEPS.map((st, i) => {
            const active = i === current
            return (
              <li key={st.key} className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => go(st.tabs[0].value)}
                  aria-current={active ? "step" : undefined}
                  className={`flex items-center gap-3 rounded-lg border px-3 py-2 text-left transition-colors hover:bg-accent ${
                    active ? "border-primary bg-primary/5 shadow-sm" : "bg-card"
                  }`}
                >
                  {done[i] ? (
                    <CheckCircle2 className="size-5 shrink-0 text-emerald-500" />
                  ) : (
                    <Circle
                      className={`size-5 shrink-0 ${
                        active ? "text-primary" : "text-muted-foreground/50"
                      }`}
                    />
                  )}
                  <span className="min-w-[5.5rem]">
                    <span className="block text-sm font-medium leading-tight">
                      {st.label}
                    </span>
                    <span className="block text-[11px] leading-tight text-muted-foreground">
                      {counts[i] || st.hint}
                    </span>
                  </span>
                </button>
                {i < STEPS.length - 1 && (
                  <span className="h-px w-5 bg-border" aria-hidden />
                )}
              </li>
            )
          })}
          <li className="ml-auto flex items-center pl-4">
            <button
              type="button"
              onClick={() => go("metrics")}
              aria-current={tab === "metrics" ? "page" : undefined}
              className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-sm transition-colors hover:bg-accent ${
                tab === "metrics" ? "border-primary bg-primary/5" : "bg-card"
              }`}
            >
              <BarChart3 className="size-4" /> Metrics
            </button>
          </li>
        </ol>
      </nav>

      <Tabs value={tab} onValueChange={go} className="gap-4">
        {tab !== "metrics" && STEPS[current].tabs.length > 1 && (
          <TabsList aria-label={`${STEPS[current].label} views`}>
            {STEPS[current].tabs.map((t) => (
              <TabsTrigger key={t.value} value={t.value}>
                {t.label}
              </TabsTrigger>
            ))}
          </TabsList>
        )}
        <TabsContent value="roles">
          <RolesPanel projectId={projectId} />
        </TabsContent>
        <TabsContent value="requirements">
          <RequirementsPanel projectId={projectId} />
        </TabsContent>
        <TabsContent value="reference">
          <ReferencePanel projectId={projectId} />
        </TabsContent>
        <TabsContent value="crawl">
          <RunsPanel projectId={projectId} />
        </TabsContent>
        <TabsContent value="model">
          <ModelPanel
            projectId={projectId}
            snapshotId={snapshot}
            view="states"
            onSnapshot={onSnapshot}
          />
        </TabsContent>
        <TabsContent value="matrix">
          <ModelPanel
            projectId={projectId}
            snapshotId={snapshot}
            view="matrix"
            onSnapshot={onSnapshot}
          />
        </TabsContent>
        <TabsContent value="log">
          <CrawlLogPanel projectId={projectId} />
        </TabsContent>
        <TabsContent value="mapping">
          <MappingPanel projectId={projectId} />
        </TabsContent>
        <TabsContent value="tests">
          <TestsPanel projectId={projectId} />
        </TabsContent>
        <TabsContent value="metrics">
          <MetricsPanel projectId={projectId} />
        </TabsContent>
      </Tabs>
    </div>
  )
}
