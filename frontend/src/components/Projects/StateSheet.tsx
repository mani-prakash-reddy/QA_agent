import { useQuery } from "@tanstack/react-query"

import { KnowledgeService } from "@/client"
import { AuthedImage, screenshotPath } from "@/components/Runs/AuthedImage"
import { Badge } from "@/components/ui/badge"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"

export function StateSheet({
  stateId,
  onClose,
}: {
  stateId: string | null
  onClose: () => void
}) {
  const { data: s } = useQuery({
    queryKey: ["state", stateId],
    queryFn: async () =>
      (
        await KnowledgeService.readState({
          path: { state_id: stateId as string },
        })
      ).data,
    enabled: !!stateId,
  })

  const destructive = s?.elements.filter((e) => e.is_destructive) ?? []
  const interactive =
    s?.elements.filter(
      (e) =>
        !e.is_destructive &&
        ["button", "link", "tab", "menuitem", "combobox"].includes(e.kind),
    ) ?? []

  return (
    <Sheet open={!!stateId} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-2xl">
        <SheetHeader>
          <SheetTitle>{s?.title ?? "Loading…"}</SheetTitle>
          <SheetDescription>
            {s
              ? `${s.kind} · ${s.route_template ?? ""} · seen by ${s.roles.join(", ") || "—"}`
              : ""}
          </SheetDescription>
        </SheetHeader>
        {s && (
          <div className="space-y-6 px-4 pb-6">
            {s.has_screenshot && (
              <AuthedImage
                path={screenshotPath(s.id)}
                alt={`Screenshot of ${s.title}`}
                className="w-full rounded-md border"
              />
            )}
            {s.nav_path.length > 0 && (
              <p className="text-sm text-muted-foreground">
                Navigation: {s.nav_path.join(" › ")}
              </p>
            )}

            {s.forms.map((f) => (
              <section key={f.id} className="space-y-2">
                <h3 className="font-semibold">
                  Form{f.name ? `: ${f.name}` : ""}{" "}
                  {f.submit_label && (
                    <span className="text-sm font-normal text-muted-foreground">
                      (submit: “{f.submit_label}”)
                    </span>
                  )}
                </h3>
                <div className="overflow-x-auto rounded-md border">
                  <table className="w-full text-sm">
                    <thead className="bg-muted/50 text-left">
                      <tr>
                        <th className="p-2">Field</th>
                        <th className="p-2">Type</th>
                        <th className="p-2">Rules</th>
                        <th className="p-2">Validation messages</th>
                      </tr>
                    </thead>
                    <tbody>
                      {f.fields.map((x) => (
                        <tr key={x.id} className="border-t align-top">
                          <td className="p-2 font-medium">
                            {x.label}
                            {x.required && (
                              <span className="text-destructive"> *</span>
                            )}
                          </td>
                          <td className="p-2">{x.input_type}</td>
                          <td className="space-y-1 p-2">
                            {x.required && (
                              <Badge variant="outline">required</Badge>
                            )}{" "}
                            {x.max_length != null && (
                              <Badge variant="outline">
                                max {x.max_length}
                              </Badge>
                            )}{" "}
                            {x.min_length != null && (
                              <Badge variant="outline">
                                min {x.min_length}
                              </Badge>
                            )}{" "}
                            {x.pattern && (
                              <Badge variant="outline">pattern</Badge>
                            )}
                            {x.options && (
                              <div className="text-xs text-muted-foreground">
                                Options:{" "}
                                {(
                                  x.options as { values: string[] }
                                ).values.join(", ")}
                              </div>
                            )}
                          </td>
                          <td className="p-2 text-xs">
                            {x.validation_messages.length === 0 ? (
                              <span className="text-muted-foreground">
                                none observed
                              </span>
                            ) : (
                              (
                                x.validation_messages as {
                                  trigger: string
                                  message: string
                                }[]
                              ).map((m) => (
                                <div key={`${m.trigger}-${m.message}`}>
                                  <span className="text-muted-foreground">
                                    {m.trigger.replace("_", " ")}:
                                  </span>{" "}
                                  “{m.message}”
                                </div>
                              ))
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            ))}

            {interactive.length > 0 && (
              <section className="space-y-2">
                <h3 className="font-semibold">
                  Controls ({interactive.length})
                </h3>
                <div className="flex flex-wrap gap-1">
                  {interactive.map((e) => (
                    <Badge key={e.id} variant="secondary">
                      {e.label || `(${e.kind})`}
                    </Badge>
                  ))}
                </div>
              </section>
            )}
            {destructive.length > 0 && (
              <section className="space-y-2">
                <h3 className="font-semibold">
                  Destructive controls (never clicked)
                </h3>
                <div className="flex flex-wrap gap-1">
                  {destructive.map((e) => (
                    <Badge key={e.id} variant="destructive">
                      {e.label}
                    </Badge>
                  ))}
                </div>
              </section>
            )}

            <section className="grid gap-4 sm:grid-cols-2">
              <div>
                <h3 className="mb-1 font-semibold">Reached from</h3>
                {s.incoming.length === 0 ? (
                  <p className="text-sm text-muted-foreground">entry point</p>
                ) : (
                  <ul className="space-y-1 text-sm">
                    {s.incoming.map((t) => (
                      <li key={t.id}>
                        {t.from_title}{" "}
                        <span className="text-muted-foreground">
                          via “{t.via_label}”
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              <div>
                <h3 className="mb-1 font-semibold">Leads to</h3>
                {s.outgoing.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    nothing recorded
                  </p>
                ) : (
                  <ul className="space-y-1 text-sm">
                    {s.outgoing.map((t) => (
                      <li key={t.id}>
                        {t.to_title}{" "}
                        <span className="text-muted-foreground">
                          via “{t.via_label}”
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </section>
          </div>
        )}
      </SheetContent>
    </Sheet>
  )
}
