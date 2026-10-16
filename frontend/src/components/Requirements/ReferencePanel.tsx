import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { BookText, FileCode2, Trash2, Upload } from "lucide-react"
import { useRef, useState } from "react"

import { ReferencesService } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

const KINDS = {
  glossary: {
    title: "Business glossary",
    icon: BookText,
    accept: ".csv,.json,.md,.txt",
    what: "Terms and their meanings (CSV, JSON, Markdown or 'Term: definition' lines). Used when requirements are read, mapped to screens and turned into test cases, so the team's own words are understood.",
  },
  api: {
    title: "API specification",
    icon: FileCode2,
    accept: ".json,.yaml,.yml",
    what: "An OpenAPI / Swagger file. Its input rules (maximum lengths, allowed values, required fields) fill in what the screens do not show, and its operations give the model the backend contract of a requirement.",
  },
} as const

function Detail({ id, kind }: { id: string; kind: "glossary" | "api" }) {
  const { data } = useQuery({
    queryKey: ["reference", id],
    queryFn: async () =>
      (await ReferencesService.readReference({ path: { reference_id: id } }))
        .data,
  })
  if (!data) return null
  if (kind === "glossary") {
    return (
      <div className="max-h-64 overflow-y-auto rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Term</TableHead>
              <TableHead>Meaning</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.terms?.map((t) => (
              <TableRow key={String(t.term)}>
                <TableCell className="font-medium">{String(t.term)}</TableCell>
                <TableCell className="text-sm">
                  {String(t.definition)}
                  {Array.isArray(t.synonyms) && t.synonyms.length > 0 && (
                    <span className="text-muted-foreground">
                      {" "}
                      (also: {t.synonyms.join(", ")})
                    </span>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    )
  }
  return (
    <div className="space-y-2">
      <p className="text-xs text-muted-foreground">
        {data.unreferenced_operations} of {data.operations?.length} operations
        are not related to any requirement by their words.
      </p>
      <div className="max-h-72 overflow-y-auto rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Operation</TableHead>
              <TableHead>Summary</TableHead>
              <TableHead>Inputs</TableHead>
              <TableHead>Requirements</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.operations?.map((o) => (
              <TableRow key={`${o.method}${o.path}`}>
                <TableCell className="font-mono text-xs">
                  <Badge variant="outline" className="mr-1">
                    {o.method}
                  </Badge>
                  {o.path}
                </TableCell>
                <TableCell className="text-sm">{o.summary}</TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {o.fields.join(", ")}
                </TableCell>
                <TableCell className="text-xs">
                  {o.related_requirements.length ? (
                    o.related_requirements.join(", ")
                  ) : (
                    <span className="text-amber-600">none</span>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}

function Slot({
  projectId,
  kind,
}: {
  projectId: string
  kind: "glossary" | "api"
}) {
  const meta = KINDS[kind]
  const Icon = meta.icon
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const input = useRef<HTMLInputElement>(null)
  const [open, setOpen] = useState(false)
  const { data: refs } = useQuery({
    queryKey: ["references", projectId],
    queryFn: async () =>
      (
        await ReferencesService.listReferences({
          path: { project_id: projectId },
        })
      ).data,
  })
  const mine = refs?.find((r) => r.kind === kind)
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["references", projectId] })
    queryClient.invalidateQueries({ queryKey: ["reference"] })
  }
  const upload = useMutation({
    mutationFn: (file: File) =>
      ReferencesService.addReference({
        path: { project_id: projectId },
        body: { kind, file },
      }),
    onSuccess: () => {
      showSuccessToast(
        "Added. It is used by the next requirement read, mapping and generation.",
      )
      refresh()
    },
    onError: handleError.bind(showErrorToast),
  })
  const remove = useMutation({
    mutationFn: (id: string) =>
      ReferencesService.deleteReference({ path: { reference_id: id } }),
    onSuccess: refresh,
    onError: handleError.bind(showErrorToast),
  })
  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-3 pb-2">
        <div>
          <CardTitle className="flex items-center gap-2 text-base">
            <Icon className="size-4" /> {meta.title}
          </CardTitle>
          <p className="mt-1 max-w-xl text-xs text-muted-foreground">
            {meta.what}
          </p>
        </div>
        <div>
          <input
            ref={input}
            type="file"
            accept={meta.accept}
            className="hidden"
            aria-label={`${meta.title} file`}
            onChange={(e) => {
              const f = e.target.files?.[0]
              if (f) upload.mutate(f)
              e.target.value = ""
            }}
          />
          <Button
            size="sm"
            variant="outline"
            onClick={() => input.current?.click()}
            disabled={upload.isPending}
          >
            <Upload className="mr-1 size-4" />
            {mine ? "Replace" : "Upload"}
          </Button>
        </div>
      </CardHeader>
      {mine && (
        <CardContent className="space-y-3">
          <div className="flex items-center justify-between gap-2 text-sm">
            <span>
              <strong>{mine.filename}</strong>{" "}
              <span className="text-muted-foreground">· {mine.summary}</span>
            </span>
            <span className="flex gap-1">
              <Button size="sm" variant="ghost" onClick={() => setOpen(!open)}>
                {open ? "Hide" : "View"}
              </Button>
              <Button
                size="icon"
                variant="ghost"
                aria-label={`Remove ${meta.title}`}
                onClick={() => remove.mutate(mine.id)}
              >
                <Trash2 className="size-4" />
              </Button>
            </span>
          </div>
          {open && <Detail id={mine.id} kind={kind} />}
        </CardContent>
      )}
    </Card>
  )
}

export function ReferencePanel({ projectId }: { projectId: string }) {
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Optional documents that make the results better. One glossary and one
        API specification per project; a new upload replaces the old one.
      </p>
      <Slot projectId={projectId} kind="glossary" />
      <Slot projectId={projectId} kind="api" />
    </div>
  )
}
