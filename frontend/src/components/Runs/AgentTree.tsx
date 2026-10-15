import type { AgentView } from "@/hooks/useRunLive"
import { BUDGET_TEXT } from "@/lib/reasons"
import { StatusBadge } from "./StatusBadge"

type Props = {
  agents: Record<string, AgentView>
  order: string[]
  selected: string | null
  onSelect: (id: string | null) => void
}

type Children = Record<string, AgentView[]>

function Node({
  agent,
  childrenOf,
  depth,
  selected,
  onSelect,
}: {
  agent: AgentView
  childrenOf: Children
  depth: number
  selected: string | null
  onSelect: (id: string | null) => void
}) {
  const active = selected === agent.id
  const kids = childrenOf[agent.id] ?? []
  return (
    <li style={{ paddingLeft: depth * 16 }}>
      <button
        type="button"
        onClick={() => onSelect(active ? null : agent.id)}
        aria-pressed={active}
        className={`flex w-full flex-col gap-1 rounded-md border p-2 text-left text-sm transition-colors hover:bg-accent ${
          active ? "border-primary bg-accent" : "border-transparent"
        }`}
      >
        <span className="flex items-center justify-between gap-2">
          <span className="font-medium capitalize">
            {agent.kind}
            {agent.role ? (
              <span className="text-muted-foreground"> · {agent.role}</span>
            ) : null}
          </span>
          <StatusBadge status={agent.status} />
        </span>
        <span className="line-clamp-2 text-xs text-muted-foreground">
          {agent.goal}
        </span>
        <span className="text-xs text-muted-foreground">
          {agent.steps} steps
          {agent.detail
            ? ` · ${BUDGET_TEXT[agent.detail] ?? agent.detail}`
            : ""}
        </span>
      </button>
      {kids.length > 0 && (
        <ul className="mt-1 space-y-1">
          {kids.map((kid) => (
            <Node
              key={kid.id}
              agent={kid}
              childrenOf={childrenOf}
              depth={depth + 1}
              selected={selected}
              onSelect={onSelect}
            />
          ))}
        </ul>
      )}
    </li>
  )
}

export function AgentTree({ agents, order, selected, onSelect }: Props) {
  const all = order.map((id) => agents[id])
  const childrenOf: Children = {}
  for (const a of all) {
    if (a.parentId && agents[a.parentId]) {
      const siblings = childrenOf[a.parentId] ?? []
      siblings.push(a)
      childrenOf[a.parentId] = siblings
    }
  }
  const roots = all.filter((a) => !a.parentId || !agents[a.parentId])
  if (roots.length === 0) {
    return (
      <p className="p-3 text-sm text-muted-foreground">
        Waiting for agents to start…
      </p>
    )
  }
  return (
    <ul className="space-y-1" aria-label="Agents">
      {roots.map((root) => (
        <Node
          key={root.id}
          agent={root}
          childrenOf={childrenOf}
          depth={0}
          selected={selected}
          onSelect={onSelect}
        />
      ))}
    </ul>
  )
}
