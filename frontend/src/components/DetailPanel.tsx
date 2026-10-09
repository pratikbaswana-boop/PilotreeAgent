import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import {
  ArrowLeftIcon,
  LightningBoltIcon,
  CheckIcon,
  PaperPlaneIcon,
} from "@radix-ui/react-icons";
import { Button } from "@/components/ui/button";
import { ChipInput } from "./ChipInput";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ApiError,
  request,
  type Analysis,
  type Enquiry,
  type User,
  type Action,
  type Tool,
  type ActionProposal,
} from "@/lib/api";
import { categories, priorities, copy, label } from "@/lib/copy";
import { SafetyBanner } from "./SafetyBanner";
const schema = z.object({
  summary: z.string().min(1).max(1000),
  category: z.enum(categories),
  priority: z.enum(priorities),
  priority_reason: z.string().min(1).max(200),
  suggested_action: z.string().min(1).max(1000),
  missing_info: z.array(z.string().max(150)).max(6),
  risk_flags: z.array(z.string().max(100)).max(6),
  needs_human_call: z.boolean(),
  recommended_tools: z
    .array(
      z.object({
        tool: z.string(),
        reason: z.string().max(200),
        is_primary: z.boolean(),
      }),
    )
    .max(3),
});
type Result = z.infer<typeof schema>;
const blank: Result = {
  summary: "",
  category: "other",
  priority: "medium",
  priority_reason: "",
  suggested_action: "",
  missing_info: [],
  risk_flags: [],
  needs_human_call: false,
  recommended_tools: [],
};
type Modal = {
  kind: "approve" | "reject" | "override_block" | "send" | "retry" | "resend";
  action?: Action;
} | null;
export function DetailPanel({
  id,
  user,
  onBack,
}: {
  id: string;
  user: User;
  onBack: () => void;
}) {
  const qc = useQueryClient();
  const enquiryQ = useQuery({
    queryKey: ["enquiry", id],
    queryFn: () => request<Enquiry>(`/enquiries/${encodeURIComponent(id)}`),
  });
  const [startedId, setStartedId] = useState<string>();
  const analysisId = startedId || enquiryQ.data?.latest_analysis_id;
  const analysisQ = useQuery({
    queryKey: ["analysis", analysisId],
    queryFn: () => request<Analysis>(`/analyses/${analysisId}`),
    enabled: !!analysisId,
    refetchInterval: (q) =>
      ["pending", "in_progress"].includes(q.state.data?.status ?? "")
        ? 2000
        : false,
  });
  const actionsQ = useQuery({
    queryKey: ["actions", id],
    queryFn: () =>
      request<Action[]>(`/actions?enquiry_id=${encodeURIComponent(id)}`),
    refetchInterval: (q) =>
      q.state.data?.some((a) => ["pending", "sending"].includes(a.status))
        ? 3000
        : false,
  });
  const toolsQ = useQuery({
    queryKey: ["tools"],
    queryFn: () => request<Tool[]>("/tools/metadata"),
    staleTime: 30000,
  });
  const proposalsQ = useQuery({
    queryKey: ["action-proposals", analysisId],
    queryFn: () =>
      request<{
        tool_execution_disabled: boolean;
        proposals: ActionProposal[];
      }>(`/analyses/${analysisId}/action-proposals`),
    enabled: !!analysisId,
  });
  const healthQ = useQuery({
    queryKey: ["health"],
    queryFn: () => request<{ breakers: Record<string, string> }>("/healthz"),
    refetchInterval: 30000,
  });
  const analysis = analysisQ.data;
  const verdicts = analysis?.safety_verdicts ?? [];
  const order = [
    "ALLOW",
    "ALLOW_WITH_WARNING",
    "REDACT",
    "QUARANTINE",
    "BLOCK",
  ];
  const decision = verdicts.reduce(
    (worst, v) =>
      order.indexOf(String(v.decision)) > order.indexOf(worst)
        ? String(v.decision)
        : worst,
    "ALLOW",
  );
  const reasons = verdicts.flatMap((v) =>
    Array.isArray(v.reason_codes) ? (v.reason_codes as string[]) : [],
  );
  const injectionCodes = new Set([
    "injection_heuristic",
    "injection_judge",
    "encoded_payload",
  ]);
  const resultRiskFlags = Array.isArray(
    (analysis?.result as Record<string, unknown> | null)?.risk_flags,
  )
    ? ((analysis?.result as Record<string, unknown>).risk_flags as string[])
    : [];
  const injectionFound =
    reasons.some((reason) => injectionCodes.has(reason)) ||
    resultRiskFlags.includes("injection_suspected");
  const injectionIntent = verdicts
    .filter(
      (verdict) =>
        Array.isArray(verdict.reason_codes) &&
        (verdict.reason_codes as string[]).some((reason) =>
          injectionCodes.has(reason),
        ),
    )
    .flatMap((verdict) =>
      Array.isArray(verdict.evidence) ? (verdict.evidence as string[]) : [],
    )[0];
  const canReview = user.role !== "viewer";
  const pending = analysis?.status === "pending_review";
  const working = ["pending", "in_progress"].includes(analysis?.status ?? "");
  const [modal, setModal] = useState<Modal>(null),
    [reason, setReason] = useState(""),
    [destinations, setDestinations] = useState<string[]>([]);
  const [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [awaitingDestination, setAwaitingDestination] = useState<string>();
  const [conflict, setConflict] = useState<{
    remote: Analysis;
    local: Result;
  } | null>(null);
  const [mergeFields, setMergeFields] = useState<string[]>([]);
  const [baseVersion, setBaseVersion] = useState<number>();
  const form = useForm<Result>({
    resolver: zodResolver(schema),
    defaultValues: blank,
  });
  const dirty = form.formState.isDirty;
  useEffect(() => {
    if (analysis && !dirty) {
      const result = analysis.result as Record<string, unknown> | null;
      form.reset({
        ...blank,
        ...result,
        priority_reason:
          String(result?.priority_reason || result?.reason || ""),
      } as Result);
      setBaseVersion(analysis.version);
    }
  }, [analysis, dirty, form]);
  useEffect(() => {
    if (!awaitingDestination || !actionsQ.data) return;
    const action = actionsQ.data.find(
      (item) => item.destination === awaitingDestination,
    );
    if (action?.status === "sent") {
      setNotice(
        awaitingDestination === "slack"
          ? "Slack alert sent successfully."
          : "Linear task created successfully" +
              (action.external_id ? ` (${action.external_id}).` : "."),
      );
      setAwaitingDestination(undefined);
    } else if (["failed", "unknown"].includes(action?.status ?? "")) {
      setError(
        `${label(awaitingDestination)} delivery needs attention. Review the delivery status below.`,
      );
      setAwaitingDestination(undefined);
    }
  }, [actionsQ.data, awaitingDestination]);
  const refresh = async () => {
    await Promise.all([
      qc.invalidateQueries({ queryKey: ["analysis"] }),
      qc.invalidateQueries({ queryKey: ["enquiry", id] }),
      qc.invalidateQueries({ queryKey: ["enquiries"] }),
      qc.invalidateQueries({ queryKey: ["actions", id] }),
      qc.invalidateQueries({ queryKey: ["action-proposals", analysisId] }),
    ]);
  };
  const mutation = useMutation({
    mutationFn: async (run: () => Promise<unknown>) => run(),
    onError: (e) => setError(e.message),
  });
  async function run(task: () => Promise<unknown>) {
    setError("");
    setNotice("");
    await mutation.mutateAsync(task);
  }
  async function analyse(force = false) {
    await run(async () => {
      const data = await request<{ analysis_id: string }>(
        `/enquiries/${encodeURIComponent(id)}/analyses`,
        "POST",
        { force },
      );
      setStartedId(data.analysis_id);
      await refresh();
    }).catch(() => {});
  }
  async function save(values: Result) {
    await run(async () => {
      const previous = qc.getQueryData<Analysis>(["analysis", analysisId]);
      qc.setQueryData(["analysis", analysisId], {
        ...analysis,
        result: values,
      });
      try {
        await request(`/analyses/${analysisId}`, "PATCH", values, {
          "If-Match": String(baseVersion),
        });
        form.reset(values);
        await refresh();
        setNotice("Changes saved.");
      } catch (e) {
        qc.setQueryData(["analysis", analysisId], previous);
        if (e instanceof ApiError && e.status === 409) {
          const remote = await request<Analysis>(`/analyses/${analysisId}`);
          setConflict({ remote, local: values });
          setMergeFields([]);
        }
        throw e;
      }
    }).catch(() => {});
  }
  function open(next: Modal) {
    setReason("");
    setError("");
    setDestinations([]);
    setModal(next);
  }
  async function confirm() {
    if (!modal || !analysis) return;
    const current = modal;
    await run(async () => {
      if (["approve", "reject", "override_block"].includes(current.kind)) {
        const values = form.getValues();
        if (current.kind !== "reject" && !(await form.trigger()))
          throw new Error(
            "Complete the required analysis fields before approving.",
          );
        const action =
          current.kind === "approve" && (dirty || !analysis.result)
            ? "edit_and_approve"
            : current.kind;
        await request(`/analyses/${analysis.id}/resume-review`, "POST", {
          action,
          expected_analysis_version: baseVersion ?? analysis.version,
          reviewer_id: user.id,
          typed_reason: reason || undefined,
          edited_fields:
            action === "edit_and_approve" || action === "override_block"
              ? values
              : undefined,
        });
        form.reset(values);
      } else if (current.kind === "retry") {
        await request(`/actions/${current.action!.id}/retry`, "POST", {
          confirm: true,
          typed_reason: reason,
        });
      } else {
        const destination =
          current.kind === "resend"
            ? current.action!.destination
            : destinations[0];
        await request("/actions", "POST", {
          analysis_id: analysis.id,
          expected_analysis_version: analysis.version,
          destinations:
            current.kind === "resend"
              ? [current.action!.destination]
              : destinations,
          resend: current.kind === "resend",
          resend_reason: reason || undefined,
          quarantine_confirmed: decision === "QUARANTINE",
        });
        setAwaitingDestination(destination);
        setNotice(
          destination === "slack"
            ? "Sending Slack alert…"
            : "Creating Linear task…",
        );
      }
      setModal(null);
      await refresh();
      if (["approve", "reject", "override_block", "retry"].includes(current.kind))
        setNotice("Updated successfully.");
    }).catch(() => {});
  }
  if (enquiryQ.isPending)
    return (
      <section className="detail-panel">
        <Skeleton className="h-8 w-1/2" />
        <Skeleton className="mt-8 h-40" />
      </section>
    );
  if (enquiryQ.isError)
    return (
      <section className="detail-panel error-state" role="alert">
        {enquiryQ.error.message}
        <Button onClick={() => enquiryQ.refetch()}>Try again</Button>
      </section>
    );
  const enquiry = enquiryQ.data;
  if (!enquiry) return null;
  const reasonRequired =
    modal &&
    (["reject", "override_block", "resend"].includes(modal.kind) ||
      (modal.kind === "retry" && modal.action?.status === "unknown") ||
      (modal.kind === "approve" && decision === "QUARANTINE"));
  const minReason =
    decision === "QUARANTINE" && modal?.kind === "approve" ? 10 : 1;
  const locked =
    !pending ||
    !canReview ||
    (decision === "BLOCK" && modal?.kind !== "override_block");
  const approved = analysis?.status === "approved";
  const result = (analysis?.result ?? blank) as Result;
  const warnings = [
    "missing_name",
    "invalid_email",
    "domain_mismatch",
    "possible_duplicate",
  ].filter((key) => enquiry[`warning_${key}` as keyof Enquiry]);
  return (
    <section className="detail-panel" aria-label="Enquiry detail">
      <header className="detail-header">
        <button className="mobile-back" onClick={onBack}>
          <ArrowLeftIcon /> Back
        </button>
        <div className="detail-title">
          <span className="eyebrow">ENQUIRY / {enquiry.id}</span>
          <h2>{enquiry.company}</h2>
          <p>
            {enquiry.name || "Name not supplied"} <span>·</span> {enquiry.email}
          </p>
        </div>
        <Button
          variant="outline"
          disabled={!canReview || mutation.isPending}
          onClick={() =>
            run(async () => {
              await request(
                `/enquiries/${encodeURIComponent(id)}/${enquiry.claimed_by === user.id ? "release" : "claim"}`,
                "POST",
              );
              await refresh();
            }).catch(() => {})
          }
        >
          {enquiry.claimed_by === user.id ? "Release claim" : "Claim enquiry"}
        </Button>
      </header>
      {enquiry.claimed_by && (
        <div className="claim-note">
          {enquiry.claimed_by === user.id
            ? "You are reviewing this enquiry."
            : "Another reviewer has claimed this enquiry."}{" "}
          {enquiry.claim_expires_at &&
            `Until ${new Date(enquiry.claim_expires_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`}
        </div>
      )}
      <div className="message-section">
        <div className="section-label">
          <h3>Original message</h3>
          <span>Source status: {enquiry.status}</span>
        </div>
        <p className="original-message">{enquiry.message}</p>
        <div className="warning-list">
          {warnings.map((w) => (
            <span key={w}>{label(w)}</span>
          ))}
        </div>
      </div>
      <div className="analysis-toolbar">
        <div>
          <h3>AI analysis</h3>
          <p>
            {analysis
              ? `${analysis.model || "Manual triage"} · Version ${analysis.version}`
              : "Turn the message into a clear next step."}
          </p>
        </div>
        <Button
          disabled={!canReview || working || mutation.isPending}
          onClick={() => analyse(!!analysis)}
        >
          <LightningBoltIcon />
          {working ? "Analysing…" : analysis ? "Run again" : "Analyse with AI"}
        </Button>
      </div>
      {(error || analysisQ.isError || actionsQ.isError) && (
        <div role="alert" className="error-note">
          {error || analysisQ.error?.message || actionsQ.error?.message}
        </div>
      )}
      {notice && (
        <div role="status" className={`success-note ${notice.includes("successfully") ? "delivery-success" : ""}`}>
          {notice.includes("successfully") && <CheckIcon />}
          <span>{notice}</span>
        </div>
      )}
      {working && (
        <div className="stepper" aria-live="polite">
          {[
            "Queued",
            "Screening",
            "Analysing",
            "Validating",
            "Awaiting review",
          ].map((phase, index) => (
            <span
              className={
                index ===
                [
                  "queued",
                  "screening",
                  "analysing",
                  "validating",
                  "awaiting_review",
                ].indexOf(analysis?.phase || "queued")
                  ? "active"
                  : ""
              }
              key={phase}
            >
              {phase}
            </span>
          ))}
          <p>Your analysis is running. You can keep browsing.</p>
        </div>
      )}
      {analysis && !working && (
        <>
          {injectionFound && (
            <section className="injection-alert" role="alert">
              <strong>Prompt Injection Detected</strong>
              <p>
                {injectionIntent ||
                  "The input attempted to manipulate the AI workflow or conceal unauthorized instructions."}
              </p>
              <span>
                Approval is not recommended. Review the enquiry manually; all
                tool actions remain disabled even if the analysis is approved.
              </span>
            </section>
          )}
          <SafetyBanner decision={decision} reasons={reasons} />
          {!analysis.result && decision !== "BLOCK" && (
            <p className="manual-note">{copy.manual}</p>
          )}
          {decision === "BLOCK" && modal?.kind !== "override_block" ? (
            <div className="blocked-panel">
              <h3>Human review required</h3>
              <p>No AI result is available for this enquiry.</p>
              {user.role === "admin" && pending && (
                <Button
                  variant="outline"
                  onClick={() => open({ kind: "override_block" })}
                >
                  Override block
                </Button>
              )}
            </div>
          ) : approved ? (
            <section className="analysis-record" aria-label="Approved analysis">
              <div className="analysis-record-heading">
                <div>
                  <span className="eyebrow">Analysis outcome</span>
                  <h3>Approved decision record</h3>
                </div>
                <span className="status-badge status-approved">
                  <CheckIcon /> Approved
                </span>
              </div>
              <div className="analysis-record-summary">
                <span>Summary</span>
                <p>{result.summary}</p>
              </div>
              <div className="analysis-record-grid">
                <div>
                  <span>Category</span>
                  <strong>{label(result.category)}</strong>
                </div>
                <div>
                  <span>Priority</span>
                  <strong className={`priority-value priority-${result.priority}`}>
                    {label(result.priority)}
                  </strong>
                </div>
              </div>
              <div className="analysis-record-block">
                <span>Why this priority</span>
                <p>{result.priority_reason}</p>
              </div>
              <div className="analysis-record-block suggested-action">
                <span>Suggested next action</span>
                <p>{result.suggested_action}</p>
              </div>
              <div className="analysis-record-lists">
                <div>
                  <span>Missing information</span>
                  <div className="record-chips">
                    {result.missing_info.length ? result.missing_info.map((item) => (
                      <span key={item}>{item}</span>
                    )) : <em>None</em>}
                  </div>
                </div>
                <div>
                  <span>Risk flags</span>
                  <div className="record-chips">
                    {result.risk_flags.length ? result.risk_flags.map((item) => (
                      <span key={item}>{item}</span>
                    )) : <em>None</em>}
                  </div>
                </div>
              </div>
              {result.needs_human_call && (
                <p className="human-call-flag">Needs a human call</p>
              )}
            </section>
          ) : (
            <form
              className={`analysis-form ${decision === "QUARANTINE" ? "unverified" : ""}`}
              onSubmit={form.handleSubmit(save)}
            >
              {decision === "QUARANTINE" && (
                <span className="watermark">UNVERIFIED</span>
              )}
              <fieldset disabled={locked || mutation.isPending}>
                <label>
                  Summary
                  <Textarea
                    {...form.register("summary")}
                    maxLength={1000}
                    rows={3}
                  />
                </label>
                <div className="field-pair">
                  <label>
                    Category
                    <select {...form.register("category")}>
                      {categories.map((c) => (
                        <option key={c} value={c}>
                          {label(c)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Priority
                    <select {...form.register("priority")}>
                      {priorities.map((p) => (
                        <option key={p}>{p}</option>
                      ))}
                    </select>
                  </label>
                </div>
                <label>
                  Priority reason
                  <Textarea
                    {...form.register("priority_reason")}
                    maxLength={200}
                    rows={2}
                  />
                </label>
                <label>
                  Suggested action
                  <Textarea
                    {...form.register("suggested_action")}
                    maxLength={1000}
                    rows={2}
                  />
                </label>
                {(["missing_info", "risk_flags"] as const).map((key) => (
                  <label key={key}>
                    {label(key)}{" "}
                    <span className="field-hint">Comma separated, up to 6</span>
                    <ChipInput
                      label={label(key)}
                      value={form.watch(key)}
                      onChange={(value) =>
                        form.setValue(key, value, { shouldDirty: true })
                      }
                    />
                  </label>
                ))}
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    {...form.register("needs_human_call")}
                  />{" "}
                  Needs a human call
                </label>
              </fieldset>
              {Object.entries(form.formState.errors).map(([key, value]) => (
                <p className="error-note" role="alert" key={key}>
                  {label(key)}: {value.message}
                </p>
              ))}
              {pending && canReview && decision !== "BLOCK" && (
                <div className="review-actions">
                  <Button
                    type="submit"
                    variant="outline"
                    disabled={!dirty || mutation.isPending}
                  >
                    Save changes
                  </Button>
                  <Button
                    type="button"
                    onClick={() => open({ kind: "approve" })}
                    disabled={mutation.isPending}
                  >
                    <CheckIcon />
                    {decision === "QUARANTINE"
                      ? "Confirm quarantine"
                      : "Approve analysis"}
                  </Button>
                </div>
              )}
            </form>
          )}
          <div className="review-status">
            <span className={`status-badge status-${analysis.status}`}>
              {label(analysis.status)}
            </span>
            {pending && canReview && (
              <button onClick={() => open({ kind: "reject" })}>
                Reject analysis
              </button>
            )}
          </div>
          <section className="send-section">
            <div className="section-label">
              <h3>Destinations</h3>
              <span>Every send is your decision</span>
            </div>
            {injectionFound && (
              <p className="tool-lock-note">
                Tool execution is locked because prompt injection was detected.
              </p>
            )}
            <div className="action-proposals">
              {(proposalsQ.data?.proposals ?? []).map((proposal) => {
                const sentAction = actionsQ.data?.find(
                  (a) => a.destination === proposal.destination && a.status === "sent",
                );
                const sent = !!sentAction;
                const disabled =
                  analysis.status !== "approved" ||
                  !proposal.executable ||
                  sent ||
                  decision === "BLOCK" ||
                  !canReview ||
                  healthQ.data?.breakers?.[`tool:${proposal.destination}`] ===
                    "open";
                return (
                  <div className={`action-proposal ${proposal.recommended ? "recommended" : ""}`} key={proposal.destination}>
                    <div>
                      <strong>{proposal.action_label}</strong>
                      {proposal.recommended && <span>Recommended</span>}
                      <p>{proposal.reason}</p>
                      {!proposal.executable && proposal.disabled_reason && (
                        <p className="proposal-disabled-reason">
                          {proposal.disabled_reason}
                        </p>
                      )}
                    </div>
                    {sentAction ? (
                      <div className="proposal-delivered">
                        <span className="status-badge status-sent"><CheckIcon /> Delivered</span>
                        <small>{sentAction.external_id || `Attempt ${sentAction.attempts}`}</small>
                        {canReview && (
                          <Button variant="ghost" size="sm" onClick={() => open({ kind: "resend", action: sentAction })}>Resend</Button>
                        )}
                      </div>
                    ) : (
                      <Button
                        variant="outline"
                        disabled={disabled}
                        onClick={() => {
                          open({ kind: "send" });
                          setDestinations([proposal.destination]);
                        }}
                      >
                        <PaperPlaneIcon /> {proposal.action_label}
                      </Button>
                    )}
                  </div>
                );
              })}
            </div>
            {proposalsQ.data?.proposals.length === 0 && (
              <p className="muted">
                No destinations configured. Ask an administrator to enable one.
              </p>
            )}
            {proposalsQ.isError && (
              <p role="alert">Destinations could not be loaded.</p>
            )}
            {!!actionsQ.data?.some((action) => action.status !== "sent") && (
            <div className="outbox-list">
              <h4>Delivery activity</h4>
              {actionsQ.data?.filter((action) => action.status !== "sent").map((action) => (
                <div className="outbox-row" key={action.id}>
                  <strong>{label(action.destination)}</strong>
                  <span className={`status-badge status-${action.status}`}>
                    {label(action.status)}
                  </span>
                  <small>
                    {action.external_id || `Attempt ${action.attempts}`}
                  </small>
                  {canReview &&
                    ["sent", "failed", "unknown"].includes(action.status) && (
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={mutation.isPending || injectionFound}
                        onClick={() =>
                          action.status === "failed"
                            ? run(async () => {
                                await request(
                                  `/actions/${action.id}/retry`,
                                  "POST",
                                  {},
                                );
                                await refresh();
                              }).catch(() => {})
                            : open({
                                kind:
                                  action.status === "sent" ? "resend" : "retry",
                                action,
                              })
                        }
                      >
                        {action.status === "sent" ? "Resend" : "Retry"}
                      </Button>
                    )}
                </div>
              ))}
            </div>
            )}
          </section>
        </>
      )}
      {!analysis && !working && (
        <div className="analysis-empty">
          <LightningBoltIcon />
          <p>The next step starts here.</p>
          <span>Run an analysis, then review before you send.</span>
        </div>
      )}
      <Dialog
        open={!!modal}
        onOpenChange={(isOpen) => {
          if (!isOpen) setModal(null);
        }}
      >
        <DialogContent className="triage-dialog">
          <DialogHeader>
            <DialogTitle>
              {modal?.kind === "approve"
                ? "Approve this analysis?"
                : modal?.kind === "send"
                  ? "Choose destinations"
                  : modal?.kind === "resend"
                    ? "Resend with a reason"
                    : modal?.kind === "retry"
                      ? "Confirm retry"
                      : modal?.kind === "override_block"
                        ? "Override blocked analysis"
                        : "Reject this analysis?"}
            </DialogTitle>
            <DialogDescription>
              {modal?.kind === "retry"
                ? copy.unknown
                : modal?.kind === "override_block"
                  ? "Complete the manual analysis fields below and record why this block should be overridden."
                  : "Review the details before confirming. This action is recorded in the enquiry history."}
            </DialogDescription>
          </DialogHeader>
          {modal?.kind === "override_block" && (
            <div className="override-fields">
              <label>
                Summary
                <Textarea {...form.register("summary")} />
              </label>
              <div className="field-pair">
                <label>
                  Category
                  <select {...form.register("category")}>
                    {categories.map((c) => (
                      <option key={c} value={c}>
                        {label(c)}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Priority
                  <select {...form.register("priority")}>
                    {priorities.map((p) => (
                      <option key={p}>{p}</option>
                    ))}
                  </select>
                </label>
              </div>
              <label>
                Priority reason
                <Textarea {...form.register("priority_reason")} />
              </label>
              <label>
                Suggested action
                <Textarea {...form.register("suggested_action")} />
              </label>
            </div>
          )}
          {modal?.kind === "send" && (
            <>
              <div className="destination-chips">
                {toolsQ.data
                  ?.filter(
                    (t) =>
                      t.enabled &&
                      !actionsQ.data?.some((a) => a.destination === t.key),
                  )
                  .map((tool) => (
                    <button
                      className={
                        destinations.includes(tool.key) ? "selected" : ""
                      }
                      aria-pressed={destinations.includes(tool.key)}
                      key={tool.key}
                      onClick={() =>
                        setDestinations((ds) =>
                          ds.includes(tool.key)
                            ? ds.filter((d) => d !== tool.key)
                            : [...ds, tool.key],
                        )
                      }
                    >
                      {tool.label}
                    </button>
                  ))}
              </div>
              <div className="payload-preview">
                <span>Payload preview</span>
                <p>{String(analysis?.result?.summary || "")}</p>
                <small>
                  {String(analysis?.result?.category || "")} ·{" "}
                  {String(analysis?.result?.priority || "")}
                </small>
              </div>
            </>
          )}
          {reasonRequired && (
            <label>
              Written reason
              <Textarea
                aria-label="Written reason"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder={
                  minReason === 10
                    ? "At least 10 characters"
                    : "Explain why this action is needed"
                }
              />
            </label>
          )}
          {error && (
            <p role="alert" className="error-note">
              {error}
            </p>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setModal(null)}>
              Cancel
            </Button>
            <Button
              onClick={confirm}
              disabled={
                mutation.isPending ||
                (!!reasonRequired && reason.trim().length < minReason) ||
                (modal?.kind === "send" && destinations.length === 0)
              }
            >
              {mutation.isPending
                ? "Working…"
                : modal?.kind === "send"
                  ? "Confirm send"
                  : "Confirm"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog
        open={!!conflict}
        onOpenChange={(open) => {
          if (!open) setConflict(null);
        }}
      >
        <DialogContent className="triage-dialog">
          <DialogHeader>
            <DialogTitle>Resolve version conflict</DialogTitle>
            <DialogDescription>{copy.conflict}</DialogDescription>
          </DialogHeader>
          {conflict &&
            Object.keys(blank)
              .filter(
                (key) =>
                  JSON.stringify(conflict.local[key as keyof Result]) !==
                  JSON.stringify(conflict.remote.result?.[key]),
              )
              .map((key) => (
                <label className="conflict-field" key={key}>
                  <input
                    type="checkbox"
                    checked={mergeFields.includes(key)}
                    onChange={() =>
                      setMergeFields((fields) =>
                        fields.includes(key)
                          ? fields.filter((f) => f !== key)
                          : [...fields, key],
                      )
                    }
                  />
                  <span>
                    Keep my {label(key).toLowerCase()}
                    <small>
                      Latest: {JSON.stringify(conflict.remote.result?.[key])}
                    </small>
                  </span>
                </label>
              ))}
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                if (conflict) {
                  form.reset({ ...blank, ...conflict.remote.result } as Result);
                  setBaseVersion(conflict.remote.version);
                  qc.setQueryData(["analysis", analysisId], conflict.remote);
                }
                setConflict(null);
                setError("");
              }}
            >
              Reload latest
            </Button>
            <Button
              onClick={() => {
                if (!conflict) return;
                const merged = {
                  ...blank,
                  ...conflict.remote.result,
                } as Result;
                for (const key of mergeFields)
                  Object.assign(merged, {
                    [key]: conflict.local[key as keyof Result],
                  });
                form.reset({ ...blank, ...conflict.remote.result } as Result);
                for (const key of Object.keys(merged) as (keyof Result)[])
                  form.setValue(key, merged[key], { shouldDirty: true });
                setBaseVersion(conflict.remote.version);
                setConflict(null);
                setError("");
                setNotice("Merge prepared. Review and save your changes.");
              }}
            >
              Prepare merge
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
