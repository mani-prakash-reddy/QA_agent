import { useEffect, useRef, useState } from "react"

import type { AgentView } from "@/hooks/useRunLive"
import { humanReason } from "@/lib/reasons"
import type { RunEvent } from "@/lib/runStream"

type Row = { icon: string; tone: string; text: string; sub?: string }

const short = (v: unknown, n = 90) => {
  const s = typeof v === "string" ? v : JSON.stringify(v)
  return s.length > n ? `${s.slice(0, n)}…` : s
}

/** Human-readable line for an event; null hides it (noise such as per-call token usage). */
export function describe(e: RunEvent): Row | null {
  const p = e.payload
  switch (e.type) {
    case "tool_call": {
      const args = Object.keys(p.args ?? {}).length
        ? ` ${short(p.args, 60)}`
        : ""
      return { icon: "→", tone: "text-foreground", text: `${p.tool}${args}` }
    }
    case "tool_result":
      return {
        icon: "←",
        tone: "text-muted-foreground",
        text: short(p.summary, 140),
      }
    case "state_discovered":
      return {
        icon: "+",
        tone: "text-emerald-600 dark:text-emerald-400",
        text: `New screen: ${p.title}`,
        sub: `${p.kind} · ${p.route} · ${p.elements} elements${p.forms ? ` · ${p.forms} form` : ""}`,
      }
    case "document_parsed":
      return {
        icon: "▤",
        tone: "text-emerald-600 dark:text-emerald-400",
        text: `Read ${p.filename}`,
        sub: `${p.format}${p.pages ? ` · ${p.pages} pages` : ""} · ${p.headings} headings · ${p.tables} tables · ${p.chunks} sections${
          p.warnings?.length ? ` · ⚠ ${p.warnings.join("; ")}` : ""
        }`,
      }
    case "mapping_started":
      return {
        icon: "▤",
        tone: "text-emerald-600 dark:text-emerald-400",
        text: `Mapping ${p.requirements} requirements to ${p.screens} screens`,
        sub: `match threshold ${Math.round(Number(p.threshold) * 100)}%`,
      }
    case "requirement_mapped": {
      const sub =
        p.outcome === "matched" || p.outcome === "weak"
          ? `${p.outcome === "weak" ? "weak match" : "matched"}: ${p.screen} (${Math.round(Number(p.confidence) * 100)}%)`
          : p.outcome === "not_ui"
            ? "not visible in the UI"
            : p.outcome === "failed"
              ? "could not be assessed"
              : "no screen shows it"
      return {
        icon: p.outcome === "matched" ? "✓" : "○",
        tone:
          p.outcome === "matched"
            ? "text-emerald-600 dark:text-emerald-400"
            : p.outcome === "failed"
              ? "text-red-600 dark:text-red-400"
              : "text-amber-600 dark:text-amber-400",
        text: `${p.req_key}: ${p.title}`,
        sub,
      }
    }
    case "gaps_computed": {
      const s = p.stats as Record<string, number>
      return {
        icon: "▤",
        tone: "text-emerald-600 dark:text-emerald-400",
        text: "Gap report updated",
        sub: `${s.requirement_gaps} requirement gaps · ${s.feature_gaps} undocumented screens`,
      }
    }
    case "generation_started":
      return {
        icon: "▤",
        tone: "text-emerald-600 dark:text-emerald-400",
        text: `Writing test cases for ${p.requirements} requirements`,
        sub: p.skipped
          ? `${p.skipped} requirements cannot be generated yet`
          : "",
      }
    case "cases_generated":
      return {
        icon: "+",
        tone: "text-emerald-600 dark:text-emerald-400",
        text: `${p.req_key}: ${p.title}`,
        sub: `${p.rule} rule-based · ${p.llm} scenario${Number(p.llm) === 1 ? "" : "s"}`,
      }
    case "requirement_extracted":
      return {
        icon: "+",
        tone: p.ambiguous
          ? "text-amber-600 dark:text-amber-400"
          : "text-emerald-600 dark:text-emerald-400",
        text: `${p.native_id ? `${p.native_id}: ` : ""}${p.title}`,
        sub: `${String(p.req_type).replace("_", " ")} · ${p.section}${p.ambiguous ? " · model flagged as ambiguous" : ""}`,
      }
    case "form_recorded":
      return {
        icon: "▤",
        tone: "text-emerald-600 dark:text-emerald-400",
        text: `Form recorded: ${(p.fields ?? []).join(", ")}`,
      }
    case "transition_recorded":
      return {
        icon: "⇢",
        tone: "text-muted-foreground",
        text: `Path: via “${p.via}” (${p.kind})`,
      }
    case "screenshot":
      return null
    case "blocked":
      return {
        icon: "⛔",
        tone: "text-red-600 dark:text-red-400",
        text:
          p.reason === "blocked_request" || p.reason === "external_request"
            ? `Blocked ${p.method} ${p.url ?? ""}`
            : `Blocked: ${humanReason(p.reason)}`,
        sub:
          p.reason === "blocked_request" || p.reason === "external_request"
            ? humanReason(p.reason)
            : p.tool,
      }
    case "skipped":
      return {
        icon: "↷",
        tone: "text-amber-600 dark:text-amber-400",
        text: `Skipped “${p.label ?? p.url ?? ""}”`,
        sub: humanReason(p.reason),
      }
    case "error":
      return {
        icon: "✕",
        tone: "text-red-600 dark:text-red-400",
        text: short(p.message, 160),
      }
    case "llm_message":
      return { icon: "✎", tone: "text-foreground", text: short(p.text, 200) }
    case "metric":
      if (p.kind === "ingest_progress") return null
      return p.kind === "validation_message"
        ? {
            icon: "✓",
            tone: "text-sky-600 dark:text-sky-400",
            text: `Validation rule on “${p.field}”`,
            sub: `${p.trigger}: ${p.message}`,
          }
        : null
    case "agent_spawned":
      return {
        icon: "◆",
        tone: "text-violet-600 dark:text-violet-400",
        text: `Agent started: ${p.spec?.kind ?? "agent"}${p.role ? ` (${p.role})` : ""}`,
        sub: p.spec?.goal,
      }
    case "agent_status":
      if (p.status === "structure_understood") {
        const per = Object.entries(p.pages ?? {})
          .map(([role, n]) => `${role}: ${n}`)
          .join(", ")
        return {
          icon: "◉",
          tone: "text-violet-600 dark:text-violet-400",
          text: "Structure understood",
          sub: `pages per role — ${per}; ${p.shared} shared by every role`,
        }
      }
      if (p.status === "plan_ready") {
        const pk = (p.packages ?? []) as {
          name: string
          role: string
          complexity: string
          part: string
          pages: number
        }[]
        return {
          icon: "▦",
          tone: "text-violet-600 dark:text-violet-400",
          text: `Plan: ${p.agents} explorer agent${Number(p.agents) === 1 ? "" : "s"}`,
          sub: pk
            .slice(0, 6)
            .map(
              (x) =>
                `${x.name}${x.part ? ` ${x.part}` : ""} → ${x.role} (${x.complexity})`,
            )
            .join(" · "),
        }
      }
      if (p.status === "coverage_report") {
        const missed = Object.entries(p.missed ?? {})
          .map(([role, n]) => `${role}: ${n}`)
          .join(", ")
        return {
          icon: "◎",
          tone: "text-violet-600 dark:text-violet-400",
          text: missed
            ? `Coverage report: pages still missing — ${missed}`
            : "Coverage report: every scouted page was explored",
        }
      }
      return p.status && !String(p.status).endsWith("_requested")
        ? {
            icon: "◇",
            tone: "text-violet-600 dark:text-violet-400",
            text: `Agent ${p.status}`,
          }
        : null
    case "agent_finished":
      return {
        icon: "■",
        tone: "text-violet-600 dark:text-violet-400",
        text: `Agent ${p.status}${p.detail ? `: ${p.detail}` : ""}`,
        sub: p.result,
      }
    case "run_status":
      return {
        icon: "▶",
        tone: "text-blue-600 dark:text-blue-400",
        text: `Run ${String(p.status).replace("_", " ")}`,
        sub: p.error ?? undefined,
      }
    default:
      return null
  }
}

export function EventFeed({
  events,
  agents,
  selected,
  detail,
  showSkipped,
}: {
  events: RunEvent[]
  agents: Record<string, AgentView>
  selected: string | null
  detail: boolean
  showSkipped: boolean
}) {
  const box = useRef<HTMLDivElement>(null)
  const [follow, setFollow] = useState(true)

  const rows = events
    .filter(
      (e) => !selected || e.agent_id === selected || e.type === "run_status",
    )
    .map((e) => ({ e, row: describe(e) }))
    .filter((r): r is { e: RunEvent; row: Row } => r.row !== null)
    .filter((r) => detail || !["tool_result", "tool_call"].includes(r.e.type))
    .filter((r) => showSkipped || r.e.type !== "skipped")

  useEffect(() => {
    if (follow && box.current) box.current.scrollTop = box.current.scrollHeight
  }, [follow])

  return (
    <div className="flex h-full flex-col">
      <div
        ref={box}
        onScroll={(ev) => {
          const el = ev.currentTarget
          setFollow(el.scrollHeight - el.scrollTop - el.clientHeight < 40)
        }}
        className="h-[28rem] overflow-y-auto rounded-md border bg-card p-2 font-mono text-xs"
        role="log"
        aria-label="Live event feed"
      >
        {rows.length === 0 && (
          <p className="p-2 text-muted-foreground">No events yet.</p>
        )}
        {rows.map(({ e, row }) => (
          <div
            key={e.seq}
            className="flex gap-2 border-b border-border/40 py-1 last:border-0"
          >
            <span className="w-14 shrink-0 text-muted-foreground">
              {new Date(e.ts).toLocaleTimeString([], { hour12: false })}
            </span>
            <span
              className={`w-4 shrink-0 text-center ${row.tone}`}
              aria-hidden
            >
              {row.icon}
            </span>
            <span className="min-w-0 flex-1">
              {e.agent_id && !selected && agents[e.agent_id]?.role ? (
                <span className="mr-1 rounded bg-muted px-1 text-[10px]">
                  {agents[e.agent_id].role}
                </span>
              ) : null}
              <span className={row.tone}>{row.text}</span>
              {row.sub ? (
                <span className="block truncate text-muted-foreground">
                  {row.sub}
                </span>
              ) : null}
            </span>
          </div>
        ))}
      </div>
      {!follow && (
        <button
          type="button"
          onClick={() => setFollow(true)}
          className="mt-1 self-end text-xs text-primary underline"
        >
          Resume auto-scroll
        </button>
      )}
    </div>
  )
}
