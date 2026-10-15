import { useQuery } from "@tanstack/react-query"
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router"

import { ProjectsService } from "@/client"
import { CrawlLogPanel } from "@/components/Projects/CrawlLogPanel"
import { ModelPanel } from "@/components/Projects/ModelPanel"
import { RolesPanel } from "@/components/Projects/RolesPanel"
import { RunsPanel } from "@/components/Projects/RunsPanel"
import { RequirementsPanel } from "@/components/Requirements/RequirementsPanel"
import { Badge } from "@/components/ui/badge"
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
  if (!project) return <p className="text-muted-foreground">Loading…</p>

  return (
    <div className="flex flex-col gap-6">
      <div>
        <p className="text-sm text-muted-foreground">
          <Link to="/projects" className="underline">
            Projects
          </Link>{" "}
          /
        </p>
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold tracking-tight">{project.name}</h1>
          {project.confidential && (
            <Badge variant="outline">confidential</Badge>
          )}
        </div>
        <p className="text-sm text-muted-foreground">
          {project.target_base_url}
        </p>
      </div>
      <Tabs
        value={tab}
        onValueChange={(t) =>
          navigate({ search: (prev: Search) => ({ ...prev, tab: t }) })
        }
      >
        <TabsList>
          <TabsTrigger value="roles">Roles</TabsTrigger>
          <TabsTrigger value="requirements">Requirements</TabsTrigger>
          <TabsTrigger value="crawl">Crawl</TabsTrigger>
          <TabsTrigger value="model">Screens</TabsTrigger>
          <TabsTrigger value="matrix">Role access</TabsTrigger>
          <TabsTrigger value="log">Crawl log</TabsTrigger>
        </TabsList>
        <TabsContent value="roles" className="pt-4">
          <RolesPanel projectId={projectId} />
        </TabsContent>
        <TabsContent value="requirements" className="pt-4">
          <RequirementsPanel projectId={projectId} />
        </TabsContent>
        <TabsContent value="crawl" className="pt-4">
          <RunsPanel projectId={projectId} />
        </TabsContent>
        <TabsContent value="model" className="pt-4">
          <ModelPanel
            projectId={projectId}
            snapshotId={snapshot}
            view="states"
            onSnapshot={(id) =>
              navigate({
                search: (prev: Search) => ({ ...prev, snapshot: id }),
              })
            }
          />
        </TabsContent>
        <TabsContent value="matrix" className="pt-4">
          <ModelPanel
            projectId={projectId}
            snapshotId={snapshot}
            view="matrix"
            onSnapshot={(id) =>
              navigate({
                search: (prev: Search) => ({ ...prev, snapshot: id }),
              })
            }
          />
        </TabsContent>
        <TabsContent value="log" className="pt-4">
          <CrawlLogPanel projectId={projectId} />
        </TabsContent>
      </Tabs>
    </div>
  )
}
