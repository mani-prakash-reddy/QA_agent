import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Plus } from "lucide-react"
import { useState } from "react"

import { RequirementsService } from "@/client"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
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

export function AddRequirement({ projectId }: { projectId: string }) {
  const [open, setOpen] = useState(false)
  const [title, setTitle] = useState("")
  const [description, setDescription] = useState("")
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const add = useMutation({
    mutationFn: () =>
      RequirementsService.createRequirement({
        path: { project_id: projectId },
        body: { title, description },
      }),
    onSuccess: () => {
      showSuccessToast("Requirement added. Review it like any other.")
      queryClient.invalidateQueries({ queryKey: ["requirements", projectId] })
      setOpen(false)
      setTitle("")
      setDescription("")
    },
    onError: handleError.bind(showErrorToast),
  })
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          <Plus className="mr-1 h-4 w-4" />
          Add requirement
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Add a requirement by hand</DialogTitle>
          <DialogDescription>
            For something the BRD does not state in words, such as a ticket or a
            verbal agreement.
          </DialogDescription>
        </DialogHeader>
        <form
          className="grid gap-3 py-2"
          onSubmit={(e) => {
            e.preventDefault()
            add.mutate()
          }}
        >
          <div className="grid gap-1">
            <Label htmlFor="a-title">Title</Label>
            <Input
              id="a-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </div>
          <div className="grid gap-1">
            <Label htmlFor="a-desc">Description</Label>
            <textarea
              id="a-desc"
              rows={4}
              className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
          <DialogFooter>
            <DialogClose asChild>
              <Button type="button" variant="outline">
                Cancel
              </Button>
            </DialogClose>
            <LoadingButton
              type="submit"
              loading={add.isPending}
              disabled={!title.trim() || !description.trim()}
            >
              Add
            </LoadingButton>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
