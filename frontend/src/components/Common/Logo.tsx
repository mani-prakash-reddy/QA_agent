import { Link } from "@tanstack/react-router"
import { ClipboardCheck } from "lucide-react"

import { cn } from "@/lib/utils"

interface LogoProps {
  variant?: "full" | "icon" | "responsive"
  className?: string
  asLink?: boolean
}

export function Logo({
  variant = "full",
  className,
  asLink = true,
}: LogoProps) {
  const icon = <ClipboardCheck className={cn("size-5 shrink-0", className)} />
  const name = <span className="font-semibold tracking-tight">QA Agent</span>

  const content =
    variant === "icon" ? (
      icon
    ) : (
      <span
        className={cn(
          "flex items-center gap-2",
          variant === "responsive" &&
            "group-data-[collapsible=icon]:[&>span]:hidden",
        )}
      >
        {icon}
        {name}
      </span>
    )

  if (!asLink) {
    return content
  }

  return <Link to="/">{content}</Link>
}
