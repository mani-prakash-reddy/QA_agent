import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { CheckCircle2, PlugZap, Trash2, XCircle } from "lucide-react"
import { useState } from "react"

import { type IntegrationPublic, IntegrationsService } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { LoadingButton } from "@/components/ui/loading-button"
import { PasswordInput } from "@/components/ui/password-input"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

export const Route = createFileRoute("/_layout/integrations")({
  component: Integrations,
  head: () => ({ meta: [{ title: "Integrations - QA Agent" }] }),
})

const META: Record<
  string,
  {
    name: string
    urlLabel: string
    urlPlaceholder: string
    userLabel: string | null
    secretLabel: string
    extra?: { key: string; label: string; placeholder: string }
  }
> = {
  jira: {
    name: "Jira Cloud",
    urlLabel: "Site URL",
    urlPlaceholder: "https://your-site.atlassian.net",
    userLabel: "Account e-mail",
    secretLabel: "API token",
  },
  testrail: {
    name: "TestRail",
    urlLabel: "TestRail URL",
    urlPlaceholder: "https://your-company.testrail.io",
    userLabel: "Login e-mail",
    secretLabel: "API key",
  },
  zephyr: {
    name: "Zephyr Scale (Cloud)",
    urlLabel: "API URL (only for another region)",
    urlPlaceholder: "https://api.zephyrscale.smartbear.com/v2",
    userLabel: null,
    secretLabel: "API access token",
    extra: {
      key: "jira_site",
      label: "Jira site (for links back to the cases)",
      placeholder: "https://your-site.atlassian.net",
    },
  },
}

function PlatformCard({ item }: { item: IntegrationPublic }) {
  const meta = META[item.platform]
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const [url, setUrl] = useState(item.base_url)
  const [user, setUser] = useState(item.username)
  const [secret, setSecret] = useState("")
  const [extra, setExtra] = useState(
    String(
      (item.settings as Record<string, string>)[meta.extra?.key ?? ""] ?? "",
    ),
  )
  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: ["integrations"] })

  const save = useMutation({
    mutationFn: () =>
      IntegrationsService.saveIntegration({
        path: { platform: item.platform },
        body: {
          base_url: url,
          username: user,
          secret: secret || null,
          settings: meta.extra && extra ? { [meta.extra.key]: extra } : {},
        },
      }),
    onSuccess: () => {
      showSuccessToast(
        "Saved. The key is stored encrypted and cannot be viewed again.",
      )
      setSecret("")
      refresh()
    },
    onError: handleError.bind(showErrorToast),
  })
  const test = useMutation({
    mutationFn: () =>
      IntegrationsService.testIntegration({
        path: { platform: item.platform },
      }),
    onSuccess: ({ data }) => {
      showSuccessToast(data.message)
      refresh()
    },
    onError: (e) => {
      handleError.call(showErrorToast, e as never)
      refresh()
    },
  })
  const remove = useMutation({
    mutationFn: () =>
      IntegrationsService.removeIntegration({
        path: { platform: item.platform },
      }),
    onSuccess: () => {
      setSecret("")
      refresh()
    },
    onError: handleError.bind(showErrorToast),
  })

  const ready =
    url.trim() !== "" || item.platform === "zephyr"
      ? (secret !== "" || item.connected) &&
        (!meta.userLabel || user.trim() !== "")
      : false

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-3 pb-2">
        <div>
          <CardTitle className="text-base">{meta.name}</CardTitle>
          <p className="mt-1 max-w-xl text-xs text-muted-foreground">
            {item.help}
          </p>
        </div>
        {item.connected ? (
          item.last_error ? (
            <Badge variant="destructive" className="gap-1">
              <XCircle className="size-3" /> needs attention
            </Badge>
          ) : (
            <Badge variant="outline" className="gap-1 border-emerald-500/50">
              <CheckCircle2 className="size-3 text-emerald-500" />
              {item.last_ok_at ? "connection verified" : "saved"}
            </Badge>
          )
        ) : (
          <Badge variant="secondary">not connected</Badge>
        )}
      </CardHeader>
      <CardContent>
        <form
          className="grid gap-3 sm:grid-cols-2"
          onSubmit={(e) => {
            e.preventDefault()
            if (ready) save.mutate()
          }}
        >
          <div className="grid gap-1.5 sm:col-span-2">
            <Label htmlFor={`${item.platform}-url`}>{meta.urlLabel}</Label>
            <Input
              id={`${item.platform}-url`}
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder={meta.urlPlaceholder}
              autoComplete="off"
            />
          </div>
          {meta.userLabel && (
            <div className="grid gap-1.5">
              <Label htmlFor={`${item.platform}-user`}>{meta.userLabel}</Label>
              <Input
                id={`${item.platform}-user`}
                value={user}
                onChange={(e) => setUser(e.target.value)}
                autoComplete="off"
              />
            </div>
          )}
          <div className="grid gap-1.5">
            <Label htmlFor={`${item.platform}-secret`}>
              {meta.secretLabel}
              {item.connected && " (leave empty to keep the saved one)"}
            </Label>
            <PasswordInput
              id={`${item.platform}-secret`}
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
              autoComplete="new-password"
            />
          </div>
          {meta.extra && (
            <div className="grid gap-1.5 sm:col-span-2">
              <Label htmlFor={`${item.platform}-extra`}>
                {meta.extra.label}
              </Label>
              <Input
                id={`${item.platform}-extra`}
                value={extra}
                onChange={(e) => setExtra(e.target.value)}
                placeholder={meta.extra.placeholder}
              />
            </div>
          )}
          {item.last_error && (
            <p className="text-sm text-red-600 sm:col-span-2" role="alert">
              {item.last_error}
            </p>
          )}
          <div className="flex flex-wrap gap-2 sm:col-span-2">
            <LoadingButton
              type="submit"
              loading={save.isPending}
              disabled={!ready}
            >
              Save
            </LoadingButton>
            <LoadingButton
              type="button"
              variant="outline"
              loading={test.isPending}
              disabled={!item.connected}
              onClick={() => test.mutate()}
            >
              <PlugZap className="mr-1 size-4" /> Test connection
            </LoadingButton>
            {item.connected && (
              <Button
                type="button"
                variant="ghost"
                className="ml-auto text-red-600"
                onClick={() => remove.mutate()}
              >
                <Trash2 className="mr-1 size-4" /> Remove
              </Button>
            )}
          </div>
        </form>
      </CardContent>
    </Card>
  )
}

function Integrations() {
  const { data } = useQuery({
    queryKey: ["integrations"],
    queryFn: async () => (await IntegrationsService.listIntegrations()).data,
  })
  return (
    <div className="flex max-w-3xl flex-col gap-5">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Integrations</h1>
        <p className="text-sm text-muted-foreground">
          Connect your own Jira, TestRail or Zephyr account once; every project
          of yours can then push its approved test cases there. Keys are stored
          encrypted, are write-only, and are never sent to an AI model.
        </p>
      </div>
      {!data && <p className="text-sm text-muted-foreground">Loading…</p>}
      {data?.map((item) => (
        <PlatformCard
          key={`${item.platform}-${item.base_url}-${item.connected}`}
          item={item}
        />
      ))}
    </div>
  )
}
