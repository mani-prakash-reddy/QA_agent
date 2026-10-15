// Live run events over Server-Sent Events.
// EventSource cannot send an Authorization header, so this reads the stream with fetch().

export type RunEvent = {
  run_id: string
  agent_id: string | null
  parent_id?: string | null
  seq: number
  ts: string
  type: string
  payload: Record<string, any>
}

export const TERMINAL_STATUSES = ["succeeded", "failed", "stopped"]

export const apiBase = () => import.meta.env.VITE_API_URL ?? ""

export const authHeaders = (): Record<string, string> => {
  const token = localStorage.getItem("access_token")
  return token ? { Authorization: `Bearer ${token}` } : {}
}

type Handlers = {
  onEvent: (event: RunEvent) => void
  onConnection: (connected: boolean) => void
  onDone: () => void
}

/** Streams events until the run reaches a terminal status. Reconnects with Last-Event-ID. Returns a stop function. */
export function openRunStream(runId: string, handlers: Handlers): () => void {
  const controller = new AbortController()
  let lastId = ""
  let finished = false

  const handleBlock = (block: string) => {
    let id = ""
    let data = ""
    for (const line of block.split("\n")) {
      if (line.startsWith("id:")) id = line.slice(3).trim()
      else if (line.startsWith("data:")) data += line.slice(5).trim()
    }
    if (!data) return
    if (id) lastId = id
    const event = JSON.parse(data) as RunEvent
    handlers.onEvent(event)
    if (
      event.type === "run_status" &&
      TERMINAL_STATUSES.includes(event.payload.status)
    ) {
      finished = true
    }
  }

  const loop = async () => {
    while (!controller.signal.aborted && !finished) {
      try {
        const response = await fetch(
          `${apiBase()}/api/v1/runs/${runId}/stream`,
          {
            headers: {
              ...authHeaders(),
              ...(lastId ? { "Last-Event-ID": lastId } : {}),
            },
            signal: controller.signal,
          },
        )
        if (!response.ok || !response.body)
          throw new Error(`HTTP ${response.status}`)
        handlers.onConnection(true)
        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ""
        for (;;) {
          const { value, done } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          let idx = buffer.indexOf("\n\n")
          while (idx >= 0) {
            handleBlock(buffer.slice(0, idx))
            buffer = buffer.slice(idx + 2)
            idx = buffer.indexOf("\n\n")
          }
        }
      } catch (_error) {
        if (controller.signal.aborted) return
      }
      handlers.onConnection(false)
      if (!finished) await new Promise((r) => setTimeout(r, 1500))
    }
    if (finished) handlers.onDone()
  }
  void loop()
  return () => controller.abort()
}
