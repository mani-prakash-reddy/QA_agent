import { Download } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import useCustomToast from "@/hooks/useCustomToast"
import {
  downloadExport,
  type ExportFormat,
  type ExportKind,
} from "@/lib/download"

const KINDS: { kind: ExportKind; label: string }[] = [
  { kind: "tests", label: "Test cases" },
  { kind: "rtm", label: "Traceability matrix" },
  { kind: "gaps", label: "Gap report" },
  { kind: "km", label: "Knowledge model" },
]
const FORMATS: ExportFormat[] = ["xlsx", "csv", "json"]

export function ExportMenu({ projectId }: { projectId: string }) {
  const { showErrorToast } = useCustomToast()
  const go = (kind: ExportKind, format: ExportFormat) =>
    downloadExport(projectId, kind, format).catch((e: Error) =>
      showErrorToast(e.message),
    )
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline">
          <Download className="mr-2 h-4 w-4" /> Export
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {KINDS.map((k, i) => (
          <div key={k.kind}>
            {i > 0 && <DropdownMenuSeparator />}
            <DropdownMenuLabel>{k.label}</DropdownMenuLabel>
            {FORMATS.map((f) => (
              <DropdownMenuItem
                key={f}
                onSelect={() => go(k.kind, f)}
                aria-label={`${k.label} as ${f}`}
              >
                {f === "xlsx" ? "Excel (.xlsx)" : f.toUpperCase()}
              </DropdownMenuItem>
            ))}
          </div>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
