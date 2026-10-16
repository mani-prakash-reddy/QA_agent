import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Settings2 } from "lucide-react"
import { type ReactNode, useEffect, useState } from "react"

import { type CrawlConfig, ProjectsService } from "@/client"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { LoadingButton } from "@/components/ui/loading-button"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

const TEST_TYPES: [string, string][] = [
  ["positive", "Positive"],
  ["negative", "Negative"],
  ["boundary", "Boundary value"],
  ["field_validation", "Field validation"],
  ["role_access", "Role-based access"],
  ["e2e", "End-to-end flow"],
  ["business_rule", "Business rule"],
]
const lines = (v: string) =>
  v
    .split("\n")
    .map((x) => x.trim())
    .filter(Boolean)
const text = (v?: string[]) => (v ?? []).join("\n")
const select = "h-9 w-full rounded-md border bg-background px-2 text-sm"

function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: ReactNode
}) {
  return (
    <div className="grid gap-1.5">
      <Label>{label}</Label>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  )
}

function Area({
  label,
  hint,
  value,
  onChange,
  placeholder,
}: {
  label: string
  hint?: string
  value: string
  onChange: (v: string) => void
  placeholder?: string
}) {
  return (
    <Field label={label} hint={hint}>
      <textarea
        aria-label={label}
        className="min-h-16 rounded-md border bg-background p-2 font-mono text-xs"
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
    </Field>
  )
}

/** Everything configurable about how a project is crawled and how its test cases are generated. */
export function ProjectSettingsDialog({
  projectId,
  trigger,
}: {
  projectId: string
  trigger?: ReactNode
}) {
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const [open, setOpen] = useState(false)
  const { data: project } = useQuery({
    queryKey: ["project", projectId],
    queryFn: async () =>
      (await ProjectsService.readProject({ path: { project_id: projectId } }))
        .data,
    enabled: open,
  })
  const [c, setC] = useState<CrawlConfig>({})
  const [globs, setGlobs] = useState({
    inc: "",
    exc: "",
    mi: "",
    me: "",
    seeds: "",
    allow: "",
  })
  const [types, setTypes] = useState<string[]>(TEST_TYPES.map(([k]) => k))
  const [llmCases, setLlmCases] = useState(4)
  const [flagged, setFlagged] = useState(false)

  useEffect(() => {
    if (!project || !open) return
    const cc = (project.crawl_config ?? {}) as CrawlConfig & {
      seeds?: string[]
    }
    setC(cc)
    setGlobs({
      inc: text(cc.include_globs),
      exc: text(cc.exclude_globs),
      mi: text(cc.include_modules),
      me: text(cc.exclude_modules),
      seeds: text(cc.seeds),
      allow: text(cc.destructive_allowlist),
    })
    const g = (project.generation_config ?? {}) as {
      test_types?: string[]
      llm_cases_per_requirement?: number
      include_flagged_requirements?: boolean
    }
    setTypes(g.test_types ?? TEST_TYPES.map(([k]) => k))
    setLlmCases(g.llm_cases_per_requirement ?? 4)
    setFlagged(g.include_flagged_requirements ?? false)
  }, [project, open])

  const num = (k: keyof CrawlConfig) => (
    <Input
      type="number"
      aria-label={String(k)}
      value={String(c[k] ?? "")}
      onChange={(e) =>
        setC({
          ...c,
          [k]: e.target.value === "" ? undefined : Number(e.target.value),
        })
      }
    />
  )
  const check = (k: keyof CrawlConfig, label: string, dflt: boolean) => (
    <label className="flex items-center gap-2 text-sm">
      <input
        type="checkbox"
        checked={Boolean(c[k] ?? dflt)}
        onChange={(e) => setC({ ...c, [k]: e.target.checked })}
      />
      {label}
    </label>
  )

  const save = useMutation({
    mutationFn: () =>
      ProjectsService.updateProject({
        path: { project_id: projectId },
        body: {
          crawl_config: {
            ...project?.crawl_config,
            ...c,
            include_globs: lines(globs.inc),
            exclude_globs: lines(globs.exc),
            include_modules: lines(globs.mi),
            exclude_modules: lines(globs.me),
            destructive_allowlist: lines(globs.allow),
            seeds: lines(globs.seeds),
          } as CrawlConfig,
          generation_config: {
            ...project?.generation_config,
            test_types: types,
            llm_cases_per_requirement: llmCases,
            include_flagged_requirements: flagged,
          } as never,
        },
      }),
    onSuccess: () => {
      showSuccessToast("Settings saved. They apply to the next run.")
      queryClient.invalidateQueries({ queryKey: ["project", projectId] })
      setOpen(false)
    },
    onError: handleError.bind(showErrorToast),
  })

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        {trigger ?? (
          <Button variant="outline">
            <Settings2 className="mr-2 size-4" /> Settings
          </Button>
        )}
      </DialogTrigger>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Crawl and generation settings</DialogTitle>
          <DialogDescription>
            Saved per project; they apply to the next crawl or generation.
          </DialogDescription>
        </DialogHeader>

        <section className="grid gap-4">
          <h3 className="text-sm font-semibold">Crawl</h3>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Field label="Maximum depth">{num("max_depth")}</Field>
            <Field label="Maximum screens">{num("max_states")}</Field>
            <Field label="Steps per agent">{num("max_steps_per_agent")}</Field>
            <Field label="Parallel agents">
              {num("max_concurrent_agents")}
            </Field>
          </div>
          <Field label="Explorer">
            <select
              aria-label="Explorer"
              className={select}
              value={c.explorer_mode ?? "deterministic"}
              onChange={(e) =>
                setC({
                  ...c,
                  explorer_mode: e.target.value as "agentic" | "deterministic",
                })
              }
            >
              <option value="deterministic">
                Deterministic (fast, no AI tokens)
              </option>
              <option value="agentic">
                Supervised agents (scouts, a plan, explorers)
              </option>
            </select>
          </Field>
          <div className="grid gap-3 sm:grid-cols-2">
            <Area
              label="Modules to include"
              hint="One per line. Empty = all."
              value={globs.mi}
              onChange={(v) => setGlobs({ ...globs, mi: v })}
            />
            <Area
              label="Modules to exclude"
              hint="One per line (a menu section name)."
              value={globs.me}
              onChange={(v) => setGlobs({ ...globs, me: v })}
            />
            <Area
              label="Only these paths"
              hint="Globs over the route, e.g. /admin/**"
              value={globs.inc}
              onChange={(v) => setGlobs({ ...globs, inc: v })}
            />
            <Area
              label="Never these paths"
              hint="e.g. /api/**, /logout"
              value={globs.exc}
              onChange={(v) => setGlobs({ ...globs, exc: v })}
            />
          </div>
          <Area
            label="Extra starting addresses"
            hint="Screens the menus do not link to, one route per line."
            value={globs.seeds}
            onChange={(v) => setGlobs({ ...globs, seeds: v })}
          />
          <div className="grid gap-2 sm:grid-cols-2">
            {check(
              "probe_forbidden_routes",
              "Probe forbidden routes (opens other roles' screens by address)",
              true,
            )}
            {check(
              "capture_static_text",
              "Capture table headers and captions (masked)",
              true,
            )}
            {check(
              "walk_wizards",
              "Walk multi-step forms (presses Next / Continue, never the final button)",
              false,
            )}
          </div>

          <div className="rounded-md border border-red-500/40 p-3">
            <h4 className="mb-2 text-sm font-semibold text-red-600">
              Destructive actions
            </h4>
            {check(
              "allow_destructive",
              "Allow delete, sign-out and payment controls to be pressed",
              false,
            )}
            <p className="mt-1 text-xs text-muted-foreground">
              Off by default and recommended off. Only use it on a throw-away
              test environment. Without it, the allow-list below can permit
              single controls.
            </p>
            <Area
              label="Allow-list (regular expressions of control labels)"
              value={globs.allow}
              onChange={(v) => setGlobs({ ...globs, allow: v })}
              placeholder="^Delete draft$"
            />
          </div>
        </section>

        <section className="grid gap-3 border-t pt-4">
          <h3 className="text-sm font-semibold">Test case generation</h3>
          <div className="flex flex-wrap gap-x-5 gap-y-2">
            {TEST_TYPES.map(([k, label]) => (
              <label key={k} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={types.includes(k)}
                  onChange={(e) =>
                    setTypes(
                      e.target.checked
                        ? [...types, k]
                        : types.filter((x) => x !== k),
                    )
                  }
                />
                {label}
              </label>
            ))}
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field
              label="Scenario cases written by the model per requirement"
              hint="Business-rule and flow cases; 0 = rule-based cases only."
            >
              <Input
                type="number"
                min={0}
                max={12}
                value={llmCases}
                onChange={(e) => setLlmCases(Number(e.target.value))}
              />
            </Field>
            <label className="flex items-center gap-2 pt-6 text-sm">
              <input
                type="checkbox"
                checked={flagged}
                onChange={(e) => setFlagged(e.target.checked)}
              />
              Also write cases for vague (flagged) requirements
            </label>
          </div>
          <p className="text-xs text-muted-foreground">
            Columns, names and the ID prefix of the export are set with the
            Template button on the Test cases tab.
          </p>
        </section>

        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <LoadingButton
            loading={save.isPending}
            onClick={() => save.mutate()}
            disabled={types.length === 0}
          >
            Save settings
          </LoadingButton>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
