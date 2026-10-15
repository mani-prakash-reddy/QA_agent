import { AlertTriangle } from "lucide-react"

import { Badge } from "@/components/ui/badge"

const STATUS_STYLE: Record<string, string> = {
  extracted: "bg-muted text-muted-foreground",
  approved: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  edited: "bg-sky-500/15 text-sky-600 dark:text-sky-400",
  rejected: "bg-zinc-500/15 text-zinc-500 line-through",
}
const STATUS_LABEL: Record<string, string> = {
  extracted: "to review",
  approved: "approved",
  edited: "edited, to approve",
  rejected: "rejected",
}

export function ReviewBadge({ status }: { status: string }) {
  return (
    <Badge
      variant="outline"
      className={`border-transparent font-medium ${STATUS_STYLE[status] ?? ""}`}
    >
      {STATUS_LABEL[status] ?? status}
    </Badge>
  )
}

const TYPE_LABEL: Record<string, string> = {
  functional: "functional",
  business_rule: "business rule",
  validation: "validation",
  acceptance_criterion: "acceptance criterion",
  nfr: "non-functional",
}

export function TypeBadge({ type }: { type: string }) {
  return <Badge variant="secondary">{TYPE_LABEL[type] ?? type}</Badge>
}

export const TYPES = Object.keys(TYPE_LABEL)
export const typeLabel = (t: string) => TYPE_LABEL[t] ?? t

export function DecisionBadge() {
  return (
    <Badge
      variant="outline"
      className="gap-1 border-amber-500/50 bg-amber-500/10 text-amber-700 dark:text-amber-400"
    >
      <AlertTriangle className="h-3 w-3" aria-hidden />
      needs decision
    </Badge>
  )
}
