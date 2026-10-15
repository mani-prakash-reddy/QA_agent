import { useEffect, useReducer, useRef } from "react"

import { RunsService } from "@/client"
import {
  openRunStream,
  type RunEvent,
  TERMINAL_STATUSES,
} from "@/lib/runStream"

export type AgentView = {
  id: string
  parentId: string | null
  kind: string
  role: string | null
  goal: string
  status: string
  detail: string
  steps: number
  startedAt: string
  finishedAt: string | null
  lastStateId: string | null
}

export type LiveState = {
  events: RunEvent[]
  agents: Record<string, AgentView>
  order: string[]
  counters: {
    states: number
    forms: number
    transitions: number
    skipped: number
    blocked: number
    errors: number
    toolCalls: number
    requirements: number
    rejected: number
    chunksDone: number
    chunksTotal: number
  }
  tokens: { input: number; output: number }
  latestShot: { stateId: string; agentId: string | null } | null
  runStatus: string
  connected: boolean
  lastSeq: number
}

const MAX_EVENTS = 4000

export const initialLive: LiveState = {
  events: [],
  agents: {},
  order: [],
  counters: {
    states: 0,
    forms: 0,
    transitions: 0,
    skipped: 0,
    blocked: 0,
    errors: 0,
    toolCalls: 0,
    requirements: 0,
    rejected: 0,
    chunksDone: 0,
    chunksTotal: 0,
  },
  tokens: { input: 0, output: 0 },
  latestShot: null,
  runStatus: "queued",
  connected: false,
  lastSeq: 0,
}

type Action =
  | { type: "events"; events: RunEvent[] }
  | { type: "connection"; connected: boolean }
  | { type: "reset" }

function apply(state: LiveState, e: RunEvent): LiveState {
  if (e.seq <= state.lastSeq) return state // idempotent: replays never double count
  const next: LiveState = {
    ...state,
    lastSeq: e.seq,
    events: [...state.events, e].slice(-MAX_EVENTS),
    counters: { ...state.counters },
    agents: { ...state.agents },
    tokens: { ...state.tokens },
  }
  const p = e.payload
  const agent = e.agent_id ? next.agents[e.agent_id] : undefined
  switch (e.type) {
    case "run_status":
      if (typeof p.status === "string" && !p.status.endsWith("_requested")) {
        next.runStatus = p.status
      }
      break
    case "agent_spawned": {
      if (!e.agent_id) break
      const spec = p.spec ?? {}
      next.agents[e.agent_id] = {
        id: e.agent_id,
        parentId: e.parent_id ?? null,
        kind: spec.kind ?? "agent",
        role: p.role ?? spec.scope?.role_ref ?? null,
        goal: spec.goal ?? "",
        status: "running",
        detail: "",
        steps: 0,
        startedAt: e.ts,
        finishedAt: null,
        lastStateId: null,
      }
      next.order = [...next.order, e.agent_id]
      break
    }
    case "agent_status":
      if (
        agent &&
        typeof p.status === "string" &&
        !p.status.endsWith("_requested")
      ) {
        next.agents[agent.id] = { ...agent, status: p.status }
      }
      break
    case "agent_finished":
      if (agent) {
        next.agents[agent.id] = {
          ...agent,
          status: p.status ?? "succeeded",
          detail: p.detail ?? "",
          steps: p.budget?.steps ?? agent.steps,
          finishedAt: e.ts,
        }
      }
      break
    case "tool_call":
      next.counters.toolCalls += 1
      if (agent) next.agents[agent.id] = { ...agent, steps: agent.steps + 1 }
      break
    case "state_discovered":
      next.counters.states += 1
      if (agent) next.agents[agent.id] = { ...agent, lastStateId: p.state_id }
      break
    case "form_recorded":
      next.counters.forms += 1
      break
    case "transition_recorded":
      next.counters.transitions += 1
      break
    case "skipped":
      if (p.reason === "ungrounded_quote") next.counters.rejected += 1
      else next.counters.skipped += 1
      break
    case "requirement_extracted":
      next.counters.requirements += 1
      break
    case "metric":
      if (p.kind === "ingest_progress") {
        next.counters.chunksDone = p.done
        next.counters.chunksTotal = p.total
      }
      break
    case "blocked":
      next.counters.blocked += 1
      break
    case "error":
      next.counters.errors += 1
      break
    case "screenshot":
      next.latestShot = { stateId: p.state_id, agentId: e.agent_id }
      break
    case "llm_usage":
      next.tokens.input += p.input_tokens ?? 0
      next.tokens.output += p.output_tokens ?? 0
      break
  }
  return next
}

function reducer(state: LiveState, action: Action): LiveState {
  switch (action.type) {
    case "events":
      return action.events.reduce(apply, state)
    case "connection":
      return { ...state, connected: action.connected }
    case "reset":
      return initialLive
  }
}

/** Live view of one run: SSE while it is active, stored events once it has finished (replay). */
export function useRunLive(runId: string, initialStatus: string | undefined) {
  const [state, dispatch] = useReducer(reducer, initialLive)
  const queue = useRef<RunEvent[]>([])
  const frame = useRef<number | null>(null)

  useEffect(() => {
    if (!initialStatus) return
    dispatch({ type: "reset" })
    queue.current = []
    let stop = () => {}
    let cancelled = false

    // Batch bursts of events into one render per animation frame.
    const flush = () => {
      frame.current = null
      if (queue.current.length) {
        dispatch({ type: "events", events: queue.current })
        queue.current = []
      }
    }
    const push = (event: RunEvent) => {
      queue.current.push(event)
      if (frame.current === null) frame.current = requestAnimationFrame(flush)
    }

    if (TERMINAL_STATUSES.includes(initialStatus)) {
      // Finished run: replay from the persisted copy of the events.
      void (async () => {
        let after = 0
        for (;;) {
          const rows = (
            await RunsService.listEvents({
              path: { run_id: runId },
              query: { after_seq: after, limit: 5000 },
            })
          ).data
          if (cancelled || rows.length === 0) break
          for (const r of rows) {
            push({
              run_id: runId,
              agent_id: r.agent_id ?? null,
              parent_id: (r.payload as any)?.parent_id ?? null,
              seq: r.seq,
              ts: r.ts,
              type: r.type,
              payload: r.payload as Record<string, any>,
            })
          }
          after = rows[rows.length - 1].seq
          if (rows.length < 5000) break
        }
        dispatch({ type: "connection", connected: false })
      })()
    } else {
      stop = openRunStream(runId, {
        onEvent: push,
        onConnection: (connected) =>
          dispatch({ type: "connection", connected }),
        onDone: () => {},
      })
    }
    return () => {
      cancelled = true
      stop()
      if (frame.current !== null) cancelAnimationFrame(frame.current)
    }
  }, [runId, initialStatus])

  return state
}
