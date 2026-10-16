import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ArrowDown, ArrowUp, Settings2 } from "lucide-react"
import { useEffect, useState } from "react"

import { ExportsService } from "@/client"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
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
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

type Col = { key: string; header: string }

/** Which columns the exports have, in what order and under which names (the test-case template). */
export function TemplateDialog({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const [open, setOpen] = useState(false)
  const [cols, setCols] = useState<Col[]>([])
  const [prefix, setPrefix] = useState("TC")
  const { data } = useQuery({
    queryKey: ["template", projectId],
    queryFn: async () =>
      (await ExportsService.readTemplate({ path: { project_id: projectId } }))
        .data,
    enabled: open,
  })
  useEffect(() => {
    if (data) {
      setCols(data.columns)
      setPrefix(data.id_prefix)
    }
  }, [data])

  const save = useMutation({
    mutationFn: () =>
      ExportsService.updateTemplate({
        path: { project_id: projectId },
        body: { columns: cols, id_prefix: prefix },
      }),
    onSuccess: () => {
      showSuccessToast("Template saved. Exports use it from now on.")
      queryClient.invalidateQueries({ queryKey: ["template", projectId] })
      setOpen(false)
    },
    onError: handleError.bind(showErrorToast),
  })

  const available = (data?.available ?? []).filter(
    (a) => !cols.some((c) => c.key === a.key),
  )
  const move = (i: number, d: number) =>
    setCols((cs) => {
      const next = [...cs]
      const [x] = next.splice(i, 1)
      next.splice(i + d, 0, x)
      return next
    })

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline">
          <Settings2 className="mr-2 h-4 w-4" /> Template
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Test-case template</DialogTitle>
          <DialogDescription>
            Columns, their order and their names in the exports. The test-case
            table in the app is not affected.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3 text-sm">
          <div className="flex items-center gap-2">
            ID prefix
            <Input
              aria-label="ID prefix"
              className="w-24"
              value={prefix}
              onChange={(e) => setPrefix(e.target.value)}
            />
            <span className="text-muted-foreground">e.g. {prefix}-0001</span>
          </div>
          <ul className="space-y-2">
            {cols.map((c, i) => (
              <li key={c.key} className="flex items-center gap-2">
                <Input
                  aria-label={`Header for ${c.key}`}
                  value={c.header}
                  onChange={(e) =>
                    setCols((cs) =>
                      cs.map((x) =>
                        x.key === c.key ? { ...x, header: e.target.value } : x,
                      ),
                    )
                  }
                />
                <Button
                  size="icon"
                  variant="ghost"
                  aria-label={`Move ${c.key} up`}
                  disabled={i === 0}
                  onClick={() => move(i, -1)}
                >
                  <ArrowUp className="h-4 w-4" />
                </Button>
                <Button
                  size="icon"
                  variant="ghost"
                  aria-label={`Move ${c.key} down`}
                  disabled={i === cols.length - 1}
                  onClick={() => move(i, 1)}
                >
                  <ArrowDown className="h-4 w-4" />
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label={`Remove ${c.key}`}
                  disabled={cols.length === 1}
                  onClick={() =>
                    setCols((cs) => cs.filter((x) => x.key !== c.key))
                  }
                >
                  Remove
                </Button>
              </li>
            ))}
          </ul>
          {available.length > 0 && (
            <div>
              <p className="mb-1 text-muted-foreground">Add a column</p>
              <div className="flex flex-wrap gap-3">
                {available.map((a) => (
                  <div key={a.key} className="flex items-center gap-1">
                    <Checkbox
                      aria-label={`Add ${a.header}`}
                      checked={false}
                      onCheckedChange={() =>
                        setCols((cs) => [
                          ...cs,
                          { key: a.key, header: a.header },
                        ])
                      }
                    />
                    {a.header}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button disabled={save.isPending} onClick={() => save.mutate()}>
            Save template
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
