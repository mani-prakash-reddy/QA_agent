import { useMutation } from "@tanstack/react-query"
import { ShieldQuestion } from "lucide-react"
import { useState } from "react"

import { RunsService } from "@/client"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import useCustomToast from "@/hooks/useCustomToast"
import type { RunEvent } from "@/lib/runStream"
import { handleError } from "@/utils"

/** The run is waiting for a person (a one-time code). Shown until the agent moves on. */
export function pendingHumanPrompt(events: RunEvent[]): RunEvent | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i]
    if (e.type === "needs_human") {
      const answered = events
        .slice(i + 1)
        .some(
          (x) =>
            x.agent_id === e.agent_id &&
            (x.type === "agent_finished" ||
              x.type === "tool_call" ||
              x.type === "state_discovered" ||
              (x.type === "agent_status" &&
                x.payload.status === "authenticated")),
        )
      return answered ? null : e
    }
  }
  return null
}

export function HumanPrompt({
  runId,
  event,
}: {
  runId: string
  event: RunEvent
}) {
  const [value, setValue] = useState("")
  const [sent, setSent] = useState(false)
  const { showErrorToast } = useCustomToast()
  const role = String(event.payload.role ?? "")
  const send = useMutation({
    mutationFn: () =>
      RunsService.humanInput({
        path: { run_id: runId },
        body: { role, value },
      }),
    onSuccess: () => setSent(true),
    onError: handleError.bind(showErrorToast),
  })

  return (
    <div
      role="alert"
      className="flex flex-wrap items-center gap-3 rounded-md border border-amber-400 bg-amber-50 p-3 text-sm dark:bg-amber-950/30"
    >
      <ShieldQuestion className="h-5 w-5 text-amber-600" />
      <span className="font-medium">
        {String(event.payload.prompt ?? "The run needs a code from you")}
      </span>
      {sent ? (
        <span className="text-muted-foreground">
          Sent. Waiting for sign-in…
        </span>
      ) : (
        <form
          className="flex items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            if (value.trim()) send.mutate()
          }}
        >
          <Input
            aria-label="One-time code"
            className="h-8 w-40"
            inputMode="numeric"
            autoComplete="one-time-code"
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
          <Button
            size="sm"
            type="submit"
            disabled={!value.trim() || send.isPending}
          >
            Send code
          </Button>
        </form>
      )}
    </div>
  )
}
