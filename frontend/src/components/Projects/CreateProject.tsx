import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { Plus } from "lucide-react"
import { useState } from "react"

import { ProjectsService } from "@/client"
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

export function CreateProject() {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState("")
  const [url, setUrl] = useState("")
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const create = useMutation({
    mutationFn: () =>
      ProjectsService.createProject({
        body: {
          name,
          target_base_url: url,
        },
      }),
    onSuccess: ({ data }) => {
      showSuccessToast("Project created")
      queryClient.invalidateQueries({ queryKey: ["projects"] })
      setOpen(false)
      navigate({ to: "/projects/$projectId", params: { projectId: data.id } })
    },
    onError: handleError.bind(showErrorToast),
  })

  const valid = name.trim() && url.trim()

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>
          <Plus className="mr-2 h-4 w-4" />
          New project
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>New project</DialogTitle>
          <DialogDescription>
            A project points at one application under test and holds its roles,
            runs and knowledge model. Point it at a test or staging environment,
            never production.
          </DialogDescription>
        </DialogHeader>
        <form
          onSubmit={(e) => {
            e.preventDefault()
            if (valid) create.mutate()
          }}
          className="grid gap-4 py-2"
        >
          <div className="grid gap-2">
            <Label htmlFor="p-name">Name</Label>
            <Input
              id="p-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Payments portal (staging)"
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="p-url">Application URL</Label>
            <Input
              id="p-url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://staging.example.com"
            />
          </div>
          <DialogFooter>
            <DialogClose asChild>
              <Button
                type="button"
                variant="outline"
                disabled={create.isPending}
              >
                Cancel
              </Button>
            </DialogClose>
            <LoadingButton
              type="submit"
              loading={create.isPending}
              disabled={!valid}
            >
              Create
            </LoadingButton>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
