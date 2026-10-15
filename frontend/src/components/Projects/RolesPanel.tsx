import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { KeyRound, Plus, Trash2 } from "lucide-react"
import { useState } from "react"

import { ProjectsService } from "@/client"
import { Badge } from "@/components/ui/badge"
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

function AddRole({ projectId }: { projectId: string }) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState("")
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [loginUrl, setLoginUrl] = useState("")
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const create = useMutation({
    mutationFn: () =>
      ProjectsService.createRole({
        path: { project_id: projectId },
        body: {
          name,
          login_recipe: loginUrl ? { login_url: loginUrl } : {},
          credentials: { username, password },
        },
      }),
    onSuccess: () => {
      showSuccessToast(
        "Role added. The password is stored encrypted and cannot be viewed again.",
      )
      queryClient.invalidateQueries({ queryKey: ["roles", projectId] })
      setOpen(false)
      setName("")
      setUsername("")
      setPassword("")
      setLoginUrl("")
    },
    onError: handleError.bind(showErrorToast),
  })
  const valid = name.trim() && username.trim() && password

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm">
          <Plus className="mr-1 h-4 w-4" />
          Add role
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Add application role</DialogTitle>
          <DialogDescription>
            A user role of the application under test (for example Admin or End
            User), with a login the crawler can use. Credentials are encrypted
            at rest and are never sent to an AI model.
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
            <Label htmlFor="r-name">Role name</Label>
            <Input
              id="r-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Admin"
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="r-user">Username or email</Label>
            <Input
              id="r-user"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="off"
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="r-pass">Password</Label>
            <PasswordInput
              id="r-pass"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="r-login">Login page path (optional)</Label>
            <Input
              id="r-login"
              value={loginUrl}
              onChange={(e) => setLoginUrl(e.target.value)}
              placeholder="/login"
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
              Save role
            </LoadingButton>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

export function RolesPanel({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient()
  const { showErrorToast } = useCustomToast()
  const { data: roles } = useQuery({
    queryKey: ["roles", projectId],
    queryFn: async () =>
      (await ProjectsService.listRoles({ path: { project_id: projectId } }))
        .data,
  })
  const remove = useMutation({
    mutationFn: (roleId: string) =>
      ProjectsService.deleteRole({
        path: { project_id: projectId, role_id: roleId },
      }),
    onError: handleError.bind(showErrorToast),
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: ["roles", projectId] }),
  })

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          The crawler explores the application once per role, signed in as that
          role.
        </p>
        <AddRole projectId={projectId} />
      </div>
      {roles && roles.length > 0 ? (
        <ul className="divide-y rounded-md border">
          {roles.map((r) => (
            <li key={r.id} className="flex items-center justify-between p-3">
              <div className="flex items-center gap-3">
                <span className="font-medium">{r.name}</span>
                {r.has_credentials ? (
                  <Badge variant="outline" className="gap-1">
                    <KeyRound className="h-3 w-3" /> credentials saved
                  </Badge>
                ) : (
                  <Badge variant="destructive">no credentials</Badge>
                )}
              </div>
              <Button
                size="icon"
                variant="ghost"
                aria-label={`Delete role ${r.name}`}
                onClick={() => remove.mutate(r.id)}
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </li>
          ))}
        </ul>
      ) : (
        <div className="rounded-md border p-8 text-center text-muted-foreground">
          No roles yet. Add at least one to start a crawl.
        </div>
      )}
    </div>
  )
}
