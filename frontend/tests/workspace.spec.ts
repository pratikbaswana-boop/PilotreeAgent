import { test, expect, type Page } from "@playwright/test";
const result = {
  summary: "A chilled delivery is overdue.",
  category: "delivery_issue",
  priority: "high",
  reason: "The expected delivery date has passed.",
  suggested_action:
    "Ask for the shipment reference and expected delivery date.",
  missing_info: ["Shipment reference"],
  risk_flags: ["time_critical"],
  needs_human_call: false,
};
export async function mockWorkspace(
  page: Page,
  options: {
    decision?: string;
    role?: string;
    analysed?: boolean;
    status?: string;
    sendStatus?: string;
    conflict?: boolean;
  } = {},
) {
  let analysis: any =
    options.analysed === false
      ? null
      : {
          id: "analysis-1",
          enquiry_id: "ENQ-1042",
          analysis_attempt: 1,
          status: options.status || "pending_review",
          result: options.decision === "BLOCK" ? null : { ...result },
          version: 1,
          model: "gemini-test",
          safety_verdicts: [
            {
              stage: "merged",
              decision: options.decision || "ALLOW",
              reason_codes: options.decision ? ["review_required"] : [],
              evidence: [],
              severity: 0,
            },
          ],
          job_id: 1,
        };
  let actions: any[] = [],
    conflict = options.conflict,
    sseCount = 0;
  const requests: { path: string; body: any }[] = [];
  const enquiry = () => ({
    id: "ENQ-1042",
    name: "Maya Singh",
    email: "maya@northline.example",
    company: "Northline Foods",
    status: "new",
    message:
      "Our chilled delivery was due yesterday and has not arrived. Can you help us confirm the next step?",
    received_at: "2026-10-09T08:10:00Z",
    created_at: "2026-10-09T08:10:00Z",
    updated_at: "2026-10-09T08:10:00Z",
    warning_missing_name: false,
    warning_invalid_email: false,
    warning_domain_mismatch: false,
    warning_possible_duplicate: false,
    duplicate_of: null,
    claimed_by: null,
    claim_expires_at: null,
    version: 1,
    latest_analysis_id: analysis?.id || null,
    analysis_status: analysis?.status || null,
    analysis_result: analysis?.result || null,
    safety_decision: options.decision || "ALLOW",
  });
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url()),
      path = url.pathname.replace("/api", ""),
      method = route.request().method(),
      body = route.request().postDataJSON();
    if (method !== "GET") requests.push({ path, body });
    const json = (data: unknown, status = 200) =>
      route.fulfill({ status, json: data });
    if (path === "/me")
      return json({
        id: "user-1",
        display_name: "Maya Chen",
        email: "maya@example.com",
        role: options.role || "reviewer",
      });
    if (path === "/healthz") return json({ status: "ok", breakers: {} });
    if (path === "/sse") {
      sseCount++;
      return route.fulfill({
        contentType: "text/event-stream",
        body: "event: connected\ndata: {}\n\n",
      });
    }
    if (path === "/tools/metadata")
      return json([
        {
          key: "slack",
          label: "Slack",
          enabled: true,
          suggested_for: ["high"],
        },
        {
          key: "linear",
          label: "Linear",
          enabled: true,
          suggested_for: ["high"],
        },
        {
          key: "sheets",
          label: "Google Sheets",
          enabled: true,
          suggested_for: [],
        },
      ]);
    if (path === "/enquiries")
      return json({
        items:
          url.searchParams.get("q") === "nothing"
            ? []
            : [
                enquiry(),
                {
                  ...enquiry(),
                  id: "ENQ-1041",
                  company: "Harbour & Co.",
                  message:
                    "Could you provide a quote for a collection next week?",
                  analysis_result: null,
                  latest_analysis_id: null,
                  analysis_status: null,
                },
                {
                  ...enquiry(),
                  id: "ENQ-1040",
                  company: "Westbrook Supply",
                  message:
                    "We need to update the contact on our next shipment.",
                  analysis_result: null,
                  latest_analysis_id: null,
                  analysis_status: null,
                },
              ],
        next_cursor: null,
      });
    if (path === "/enquiries/ENQ-1042") return json(enquiry());
    if (path === "/enquiries/ENQ-1042/analyses") {
      analysis = {
        id: "analysis-1",
        enquiry_id: "ENQ-1042",
        analysis_attempt: 1,
        status: "pending_review",
        result: { ...result },
        version: 1,
        model: "gemini-test",
        safety_verdicts: [
          {
            stage: "merged",
            decision: options.decision || "ALLOW",
            reason_codes: [],
            evidence: [],
            severity: 0,
          },
        ],
        job_id: 1,
      };
      return json(
        {
          analysis_id: "analysis-1",
          job_id: 1,
          attached: false,
          reused: false,
        },
        202,
      );
    }
    if (path === "/analyses/analysis-1" && method === "GET")
      return json(analysis);
    if (path === "/analyses/analysis-1" && method === "PATCH") {
      if (conflict) {
        conflict = false;
        analysis = {
          ...analysis,
          version: 2,
          result: {
            ...analysis.result,
            summary: "Changed by another reviewer.",
          },
        };
        return json({ detail: { current: 2 } }, 409);
      }
      analysis = { ...analysis, version: analysis.version + 1, result: body };
      return json(analysis);
    }
    if (path === "/analyses/analysis-1/resume-review") {
      analysis = {
        ...analysis,
        status: body.action === "reject" ? "rejected" : "approved",
        version: analysis.version + 1,
        result: body.edited_fields || analysis.result,
      };
      return json(analysis);
    }
    if (path === "/actions" && method === "GET") return json(actions);
    if (path === "/actions" && method === "POST") {
      actions = [
        ...actions,
        ...body.destinations.map((destination: string) => ({
          id: `action-${actions.length + 1}`,
          destination,
          status: options.sendStatus || "sent",
          attempts: 1,
          external_id: null,
          created_at: new Date().toISOString(),
        })),
      ];
      return json({ actions }, 202);
    }
    if (path.match(/^\/actions\/.*\/retry$/)) {
      actions = actions.map((a) => ({
        ...a,
        status: "sent",
        attempts: a.attempts + 1,
      }));
      return json({ status: "pending" }, 202);
    }
    return json({ detail: "Not found" }, 404);
  });
  return { requests, getSseCount: () => sseCount };
}
async function openEnquiry(page: Page) {
  await page.goto("/enquiries");
  await page.getByRole("button", { name: "Open Northline Foods" }).click();
  await expect(
    page.getByRole("heading", { name: "Northline Foods" }),
  ).toBeVisible();
}
async function approve(page: Page) {
  await page
    .getByRole("button", { name: "Approve analysis", exact: true })
    .click();
  await page.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(
    page
      .getByRole("region", { name: "Enquiry detail" })
      .getByText("Approved", { exact: true }),
  ).toBeVisible();
}
async function send(page: Page) {
  await page.getByRole("button", { name: "Slack", exact: true }).click();
  await page.getByRole("button", { name: "Confirm send", exact: true }).click();
}
test("analyse, edit, approve, send, and deliberate resend", async ({
  page,
}) => {
  const state = await mockWorkspace(page, { analysed: false });
  await openEnquiry(page);
  await page.getByRole("button", { name: "Analyse with AI" }).click();
  await expect(page.getByLabel("Summary", { exact: true })).toHaveValue(
    result.summary,
  );
  await page
    .getByLabel("Summary", { exact: true })
    .fill("Delivery needs a human follow-up.");
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByRole("status")).toContainText("Changes saved");
  await approve(page);
  await send(page);
  await expect(page.getByText("Sent", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Resend", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Confirm", exact: true }),
  ).toBeDisabled();
  await page
    .getByLabel("Written reason")
    .fill("Send to the updated operations channel.");
  await page.getByRole("button", { name: "Confirm", exact: true }).click();
  expect(state.requests.filter((r) => r.path === "/actions")).toHaveLength(2);
});
test("quarantine requires typed confirmation", async ({ page }) => {
  const state = await mockWorkspace(page, { decision: "QUARANTINE" });
  await openEnquiry(page);
  await expect(page.getByText("UNVERIFIED", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Confirm quarantine" }).click();
  await expect(
    page.getByRole("button", { name: "Confirm", exact: true }),
  ).toBeDisabled();
  await page
    .getByLabel("Written reason")
    .fill("Verified against the original enquiry.");
  await page.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(
    page
      .getByRole("region", { name: "Enquiry detail" })
      .getByText("Approved", { exact: true }),
  ).toBeVisible();
  expect(state.requests.at(-1)?.body.typed_reason).toContain("Verified");
});
test("blocked reviewer cannot edit or send", async ({ page }) => {
  await mockWorkspace(page, { decision: "BLOCK" });
  await openEnquiry(page);
  await expect(
    page.getByText("Analysis blocked", { exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("Summary", { exact: true })).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Override block" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Slack", exact: true }),
  ).toBeDisabled();
});
test("admin block override collects manual result and reason", async ({
  page,
}) => {
  const state = await mockWorkspace(page, { decision: "BLOCK", role: "admin" });
  await openEnquiry(page);
  await page.getByRole("button", { name: "Override block" }).click();
  const dialog = page.getByRole("dialog");
  await dialog
    .getByLabel("Summary", { exact: true })
    .fill("Manually reviewed enquiry");
  await dialog.getByLabel("Reason", { exact: true }).fill("Content verified");
  await dialog
    .getByLabel("Suggested action", { exact: true })
    .fill("Contact the customer");
  await dialog
    .getByLabel("Written reason")
    .fill("False positive verified by the administrator.");
  await dialog.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(
    page
      .getByRole("region", { name: "Enquiry detail" })
      .getByText("Approved", { exact: true }),
  ).toBeVisible();
  expect(state.requests.at(-1)?.body.action).toBe("override_block");
});
test("unknown send requires explicit retry confirmation", async ({ page }) => {
  const state = await mockWorkspace(page, {
    status: "approved",
    sendStatus: "unknown",
  });
  await openEnquiry(page);
  await send(page);
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(
    page.getByText(/Retrying may send it a second time/),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Confirm", exact: true }),
  ).toBeDisabled();
  await page
    .getByLabel("Written reason")
    .fill("Checked Slack; no message arrived.");
  await page.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(page.getByText("Sent", { exact: true })).toBeVisible();
  expect(state.requests.at(-1)?.body.confirm).toBe(true);
});
test("failed send retries without confirmation", async ({ page }) => {
  await mockWorkspace(page, { status: "approved", sendStatus: "failed" });
  await openEnquiry(page);
  await send(page);
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(page.getByText("Sent", { exact: true })).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
});
test("concurrent edit conflict reloads latest value", async ({ page }) => {
  await mockWorkspace(page, { conflict: true });
  await openEnquiry(page);
  await page.getByLabel("Summary", { exact: true }).fill("My local edit");
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(
    page.getByRole("heading", { name: "Resolve version conflict" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Reload latest" }).click();
  await expect(page.getByLabel("Summary", { exact: true })).toHaveValue(
    "Changed by another reviewer.",
  );
});
test("filters update URL and empty state can be cleared", async ({ page }) => {
  await mockWorkspace(page);
  await page.goto("/enquiries");
  await page.getByRole("textbox", { name: "Search enquiries" }).fill("nothing");
  await expect(page).toHaveURL(/q=nothing/);
  await expect(
    page.getByText("No enquiries match these filters"),
  ).toBeVisible();
  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(
    page.getByRole("button", { name: "Open Northline Foods" }),
  ).toBeVisible();
});
test("viewer has read-only analysis", async ({ page }) => {
  await mockWorkspace(page, { role: "viewer" });
  await openEnquiry(page);
  await expect(page.getByLabel("Summary", { exact: true })).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Approve analysis" }),
  ).toHaveCount(0);
});
test("SSE reconnects after server closes", async ({ page }) => {
  const state = await mockWorkspace(page);
  await page.goto("/enquiries");
  await expect.poll(() => state.getSseCount()).toBeGreaterThan(1);
});
test("desktop and mobile workspace fit viewport", async ({ page }) => {
  await mockWorkspace(page);
  await openEnquiry(page);
  await page.screenshot({
    path: "test-results/workspace-desktop.png",
    fullPage: true,
    animations: "disabled",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("heading", { name: "Northline Foods" }),
  ).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await page.screenshot({
    path: "test-results/workspace-mobile.png",
    fullPage: true,
    animations: "disabled",
  });
  await page.getByRole("button", { name: "Back", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Open Northline Foods" }),
  ).toBeVisible();
});

for (const [decision, title] of [
  ["ALLOW_WITH_WARNING", "Review with care"],
  ["REDACT", "Personal information masked"],
]) {
  test(`${decision} safety banner remains reviewable`, async ({ page }) => {
    await mockWorkspace(page, { decision });
    await openEnquiry(page);
    await expect(page.getByText(title, { exact: true })).toBeVisible();
    await expect(page.getByLabel("Summary", { exact: true })).toBeEnabled();
  });
}
test("session expiry redirects to sign-in", async ({ page }) => {
  await mockWorkspace(page);
  await page.route("**/api/me", (route) =>
    route.fulfill({ status: 401, json: { detail: "Session expired" } }),
  );
  await page.goto("/enquiries");
  await expect(page).toHaveURL(/\/login$/);
  await expect(
    page.getByRole("heading", { name: /A clear next step/ }),
  ).toBeVisible();
});
test("dark theme is persistent and readable", async ({ page }) => {
  await mockWorkspace(page);
  await openEnquiry(page);
  await page.getByRole("button", { name: "Toggle theme" }).click();
  await expect(page.locator("html")).toHaveClass("dark");
  await page.screenshot({
    path: "test-results/workspace-dark.png",
    fullPage: true,
    animations: "disabled",
  });
  await page.reload();
  await expect(page.locator("html")).toHaveClass("dark");
  await expect(
    page.getByRole("heading", { name: "Northline Foods" }),
  ).toBeVisible();
});
test("manual conflict merge keeps selected local fields", async ({ page }) => {
  await mockWorkspace(page, { conflict: true });
  await openEnquiry(page);
  await page.getByLabel("Summary", { exact: true }).fill("My local summary");
  await page.getByRole("button", { name: "Save changes" }).click();
  await page.getByLabel("Keep my summary").check();
  await page.getByRole("button", { name: "Prepare merge" }).click();
  await expect(page.getByLabel("Summary", { exact: true })).toHaveValue(
    "My local summary",
  );
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByRole("status")).toContainText("Changes saved");
});
