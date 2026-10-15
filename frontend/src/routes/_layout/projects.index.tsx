import { useQuery } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"

import { ProjectsService } from "@/client"
import { CreateProject } from "@/components/Projects/CreateProject"
import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"

export const Route = createFileRoute("/_layout/projects/")({
  component: Projects,
  head: () => ({ meta: [{ title: "Projects - QA Agent" }] }),
})

function Projects() {
  const { data: projects, isLoading } = useQuery({
    queryKey: ["projects"],
    queryFn: async () => (await ProjectsService.listProjects()).data,
  })

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Projects</h1>
          <p className="text-muted-foreground">
            Each project is one application under test.
          </p>
        </div>
        <CreateProject />
      </div>
      {isLoading ? (
        <p className="text-muted-foreground">Loading…</p>
      ) : projects && projects.length > 0 ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {projects.map((p) => (
            <Link
              key={p.id}
              to="/projects/$projectId"
              params={{ projectId: p.id }}
            >
              <Card className="h-full transition-colors hover:bg-accent">
                <CardHeader>
                  <CardTitle className="flex items-center justify-between gap-2 text-lg">
                    {p.name}
                    {p.confidential && (
                      <Badge variant="outline">confidential</Badge>
                    )}
                  </CardTitle>
                  <CardDescription className="truncate">
                    {p.target_base_url}
                  </CardDescription>
                </CardHeader>
                <CardContent className="text-xs text-muted-foreground">
                  Created {new Date(p.created_at).toLocaleDateString()}
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      ) : (
        <div className="rounded-md border p-10 text-center">
          <h3 className="text-lg font-semibold">No projects yet</h3>
          <p className="text-muted-foreground">
            Create one to add roles and start a crawl.
          </p>
        </div>
      )}
    </div>
  )
}
