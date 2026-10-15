// Why the crawler skipped or blocked something, in words a QA person can act on.
export const REASONS: Record<string, string> = {
  shell_toggle: "app chrome control (sidebar, theme), not application function",
  repeated_row_action: "same control as the first table row",
  repeated_component: "one of many look-alike controls (explored once)",
  external_link: "leads to another website",
  external_navigation: "navigated away from the application (undone)",
  external_url: "outside the application",
  external_request: "request to another website, blocked",
  duplicate_state: "screen already recorded",
  submit_skipped: "safe mode never submits forms",
  blocked_request: "state-changing request aborted by safe mode",
  toggle_skipped: "toggles can save instantly, so they are not flipped",
  disabled: "control is disabled",
  element_gone: "control disappeared before it could be used",
  budget: "stopped by the step budget",
  max_states: "reached the maximum number of screens",
  depth: "deeper than the configured depth",
  excluded_by_config: "excluded by the project configuration",
  click_cap: "too many controls on one screen; the rest were not clicked",
  reopen_failed: "could not reopen the menu to continue",
  auth: "login failed",
  ungrounded_quote:
    "the model returned a requirement whose quote is not in the document, so it was discarded",
}

export function humanReason(reason: string | null | undefined): string {
  if (!reason) return ""
  if (reason.startsWith("denylist")) return "destructive action, never clicked"
  return REASONS[reason] ?? reason.replace(/_/g, " ")
}

export const BUDGET_TEXT: Record<string, string> = {
  steps: "step limit reached",
  tokens: "token limit reached",
  seconds: "time limit reached",
  cost: "cost limit reached",
}
