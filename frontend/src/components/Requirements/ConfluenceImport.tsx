import { useMutation } from "@tanstack/react-query"
import { Link2 } from "lucide-react"
import { useState } from "react"

import { BrdService } from "@/client"
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
import { PasswordInput } from "@/components/ui/password-input"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

export function ConfluenceImport({
  projectId,
  onDone,
}: {
  projectId: string
  onDone: () => void
}) {
  const [open, setOpen] = useState(false)
  const [pageUrl, setPageUrl] = useState("")
  const [email, setEmail] = useState("")
  const [token, setToken] = useState("")
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const run = useMutation({
    mutationFn: () =>
      BrdService.importConfluence({
        path: { project_id: projectId },
        body: {
          page_url: pageUrl.trim(),
          email: email.trim() || null,
          api_token: token,
        },
      }),
    onSuccess: () => {
      showSuccessToast("Imported. Requirements are being extracted.")
      setOpen(false)
      setToken("")
      onDone()
    },
    onError: handleError.bind(showErrorToast),
  })

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline">
          <Link2 className="mr-2 h-4 w-4" />
          Import from Confluence
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Import a Confluence page</DialogTitle>
          <DialogDescription>
            The page becomes the BRD. The token is used for this request only
            and is not stored.
          </DialogDescription>
        </DialogHeader>
        <form
          className="grid gap-4 py-2"
          onSubmit={(e) => {
            e.preventDefault()
            if (pageUrl.trim() && token) run.mutate()
          }}
        >
          <div className="grid gap-2">
            <Label htmlFor="c-url">Page URL</Label>
            <Input
              id="c-url"
              value={pageUrl}
              onChange={(e) => setPageUrl(e.target.value)}
              placeholder="https://site.atlassian.net/wiki/spaces/KEY/pages/12345/Title"
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="c-email">Account e-mail (Cloud only)</Label>
            <Input
              id="c-email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="off"
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="c-token">API token</Label>
            <PasswordInput
              id="c-token"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              autoComplete="off"
            />
          </div>
          <DialogFooter>
            <DialogClose asChild>
              <Button type="button" variant="outline" disabled={run.isPending}>
                Cancel
              </Button>
            </DialogClose>
            <LoadingButton
              type="submit"
              loading={run.isPending}
              disabled={!pageUrl.trim() || !token}
            >
              Import
            </LoadingButton>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
