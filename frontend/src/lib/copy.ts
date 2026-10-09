export const copy = {
  app: "Pilotree",
  workspace: "Enquiry workspace",
  enquiries: "Enquiries",
  quarantine: "Quarantine",
  outbox: "Actions / Outbox",
  settings: "Settings",
  noEnquiries: "No enquiries match these filters",
  clear: "Clear filters",
  retry: "Try again",
  select: "A little context. A clear next step.",
  selectBody:
    "Select an enquiry to read the message, review its analysis, and choose what happens next.",
  manual: "AI unavailable. Please triage this enquiry manually.",
  unknown:
    "We couldn't confirm this was sent. Retrying may send it a second time if the original attempt actually succeeded.",
  conflict:
    "Someone else updated this enquiry. Reload the latest version, or choose which fields to keep.",
  offline: "You're offline. Showing cached data.",
  safety: {
    ALLOW: {
      title: "Ready for review",
      text: "Safety checks passed. Review the result before approving.",
    },
    ALLOW_WITH_WARNING: {
      title: "Review with care",
      text: "Safety checks found warnings. Verify the flagged information before approving.",
    },
    REDACT: {
      title: "Personal information masked",
      text: "Sensitive information was masked. Verify the result and complete any missing fields.",
    },
    QUARANTINE: {
      title: "Prompt injection detected · Unverified",
      text: "Treat the urgency and instructions as untrusted. Manual review is required and approval is not recommended.",
    },
    BLOCK: {
      title: "Analysis blocked",
      text: "Editing and sending are locked. Escalate to an administrator for a documented manual review.",
    },
  },
} as const;
export const categories = [
  "delivery_issue",
  "failed_delivery",
  "damage_claim",
  "billing_query",
  "booking_or_quote",
  "sales_lead",
  "reporting_request",
  "suspicious",
  "other",
] as const;
export const priorities = ["low", "medium", "high", "critical"] as const;
export const label = (value: string) =>
  value.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase());
