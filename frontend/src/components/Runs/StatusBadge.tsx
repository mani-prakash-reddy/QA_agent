import { Badge } from "@/components/ui/badge"

const STYLES: Record<string, string> = {
  queued: "bg-muted text-muted-foreground",
  running: "bg-blue-500/15 text-blue-600 dark:text-blue-400",
  authenticated: "bg-blue-500/15 text-blue-600 dark:text-blue-400",
  paused: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
  waiting_human: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
  succeeded: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  ready: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  failed: "bg-red-500/15 text-red-600 dark:text-red-400",
  stopped: "bg-zinc-500/15 text-zinc-600 dark:text-zinc-400",
  budget_exceeded: "bg-orange-500/15 text-orange-600 dark:text-orange-400",
}

const GLYPH: Record<string, string> = {
  running: "●",
  authenticated: "●",
  paused: "❚❚",
  succeeded: "✓",
  ready: "✓",
  failed: "✕",
  stopped: "■",
  budget_exceeded: "!",
  queued: "○",
}

/** Status is conveyed by text and glyph, never by colour alone. */
export function StatusBadge({ status }: { status: string }) {
  return (
    <Badge
      variant="outline"
      className={`border-transparent gap-1 font-medium ${STYLES[status] ?? ""}`}
    >
      <span aria-hidden>{GLYPH[status] ?? "·"}</span>
      {status.replace("_", " ")}
    </Badge>
  )
}
