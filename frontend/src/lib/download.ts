import { apiBase, authHeaders } from "@/lib/runStream"

export type ExportKind = "tests" | "rtm" | "gaps" | "km"
export type ExportFormat = "xlsx" | "csv" | "json"

/** Download an export. The request carries the auth header, so it cannot be a plain link. */
export async function downloadExport(
  projectId: string,
  kind: ExportKind,
  format: ExportFormat,
  params: Record<string, string> = {},
) {
  const qs = new URLSearchParams({ format, ...params })
  const r = await fetch(
    `${apiBase()}/api/v1/projects/${projectId}/exports/${kind}?${qs}`,
    { headers: authHeaders() },
  )
  if (!r.ok) {
    const body = await r.json().catch(() => ({}))
    throw new Error(body.detail ?? `Export failed (${r.status})`)
  }
  const url = URL.createObjectURL(await r.blob())
  const a = document.createElement("a")
  a.href = url
  a.download = `${{ tests: "test-cases", rtm: "rtm", gaps: "gap-report", km: "knowledge-model" }[kind]}.${format}`
  a.click()
  URL.revokeObjectURL(url)
}
