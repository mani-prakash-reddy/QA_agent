import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Pause, Play, Square } from "lucide-react"

import { RunsService } from "@/client"
import { Button } from "@/components/ui/button"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

export function RunControls({
  runId,
  status,
}: {
  runId: string
  status: string
}) {
  const queryClient = useQueryClient()
  const { showErrorToast } = useCustomToast()
  const send = useMutation({
    mutationFn: (action: "pause" | "resume" | "stop") =>
      RunsService.controlRun({ path: { run_id: runId }, body: { action } }),
    onError: handleError.bind(showErrorToast),
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: ["run", runId] }),
  })
  const live = ["queued", "running", "paused", "waiting_human"].includes(status)
  if (!live) return null
  return (
    <div className="flex gap-2">
      {status === "paused" ? (
        <Button
          size="sm"
          variant="outline"
          onClick={() => send.mutate("resume")}
          disabled={send.isPending}
        >
          <Play className="mr-1 h-4 w-4" /> Resume
        </Button>
      ) : (
        <Button
          size="sm"
          variant="outline"
          onClick={() => send.mutate("pause")}
          disabled={send.isPending || status === "queued"}
        >
          <Pause className="mr-1 h-4 w-4" /> Pause
        </Button>
      )}
      <Button
        size="sm"
        variant="destructive"
        onClick={() => send.mutate("stop")}
        disabled={send.isPending}
      >
        <Square className="mr-1 h-4 w-4" /> Stop
      </Button>
    </div>
  )
}
