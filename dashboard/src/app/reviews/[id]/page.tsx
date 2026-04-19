export const dynamic = "force-dynamic";
import { api } from "@/lib/api";
import { format } from "date-fns";
import { CheckCircle, XCircle, GitPullRequest, FileCode } from "lucide-react";
import Link from "next/link";
import Badge, { classificationVariant, severityVariant, stateVariant } from "@/components/Badge";
import { notFound } from "next/navigation";

export default async function ReviewDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  let review;
  try {
    review = await api.review(Number(id));
  } catch {
    notFound();
  }

  const pr = review.pull_request;
  const isApproved = review.approved;
  const severityOrder = ["critical", "high", "medium", "low"];

  return (
    <div className="p-8 max-w-4xl space-y-6">
      {/* Header */}
      <div className="flex items-start gap-4">
        {isApproved
          ? <CheckCircle size={32} style={{ color: "var(--success)", flexShrink: 0 }} />
          : <XCircle size={32} style={{ color: "var(--danger)", flexShrink: 0 }} />}
        <div className="space-y-1">
          <h1 className="text-xl font-bold" style={{ color: "var(--text)" }}>
            {pr.title || `PR #${pr.number}`}
          </h1>
          <div className="flex flex-wrap items-center gap-2 text-xs" style={{ color: "var(--text-muted)" }}>
            <span className="flex items-center gap-1"><GitPullRequest size={12} />#{pr.number}</span>
            <span>·</span>
            <span>@{pr.author}</span>
            <span>·</span>
            <span>{pr.repo}</span>
            <span>·</span>
            <span>{format(new Date(review.reviewed_at), "MMM d, yyyy 'at' HH:mm")}</span>
            {pr.plane_issue_id && (
              <>
                <span>·</span>
                <span className="text-indigo-400">Plane #{pr.plane_issue_id}</span>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Badges row */}
      <div className="flex flex-wrap gap-2">
        <Badge variant={isApproved ? "success" : "danger"}>
          {isApproved ? "✓ Approved" : "✗ Changes Requested"}
        </Badge>
        <Badge variant={classificationVariant(review.classification)}>{review.classification}</Badge>
        <Badge variant={stateVariant(review.plane_state)}>{review.plane_state.replace("_", " ")}</Badge>
        <Badge variant="muted">+{pr.additions} / -{pr.deletions} lines</Badge>
      </div>

      {/* Summary card */}
      <div className="rounded-xl border p-5 space-y-3"
        style={{ background: "var(--surface)", borderColor: "var(--border)" }}>
        <h2 className="text-sm font-semibold" style={{ color: "var(--text)" }}>Summary</h2>
        <p className="text-sm leading-relaxed" style={{ color: "var(--text-muted)" }}>{review.summary || "—"}</p>
        {review.suggestion && (
          <div className="rounded-lg p-3 text-sm" style={{ background: "rgba(99,102,241,0.08)", color: "var(--accent-hover)", borderLeft: "3px solid var(--accent)" }}>
            <strong>Suggestion:</strong> {review.suggestion}
          </div>
        )}
      </div>

      {/* Issue counts */}
      <div className="grid grid-cols-4 gap-3">
        {severityOrder.map((s) => {
          const count = review.issues_by_severity[s as keyof typeof review.issues_by_severity];
          return (
            <div key={s} className="rounded-xl border p-4 text-center"
              style={{ background: "var(--surface)", borderColor: "var(--border)" }}>
              <p className="text-2xl font-bold">
                <Badge variant={severityVariant(s)} className="text-xl px-3 py-1">{count}</Badge>
              </p>
              <p className="text-xs mt-1 capitalize" style={{ color: "var(--text-muted)" }}>{s}</p>
            </div>
          );
        })}
      </div>

      {/* Issues list */}
      {review.issues.length > 0 && (
        <div className="rounded-xl border overflow-hidden"
          style={{ background: "var(--surface)", borderColor: "var(--border)" }}>
          <div className="px-5 py-3 border-b" style={{ borderColor: "var(--border)" }}>
            <h2 className="text-sm font-semibold" style={{ color: "var(--text)" }}>
              Issues Found ({review.issues.length})
            </h2>
          </div>
          <div className="divide-y" style={{ borderColor: "var(--border)" }}>
            {review.issues.map((issue, i) => (
              <div key={i} className="px-5 py-4 flex gap-4">
                <Badge variant={severityVariant(issue.severity)} className="self-start mt-0.5 flex-shrink-0">
                  {issue.severity}
                </Badge>
                <div className="space-y-1 min-w-0">
                  <div className="flex items-center gap-2 text-xs flex-wrap" style={{ color: "var(--text-muted)" }}>
                    <span className="flex items-center gap-1">
                      <FileCode size={11} />
                      <code style={{ color: "var(--accent-hover)" }}>{issue.file}</code>
                    </span>
                    {issue.line && <span>line {issue.line}</span>}
                  </div>
                  <p className="text-sm" style={{ color: "var(--text)" }}>{issue.comment}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="pt-2">
        <Link href={`/repos/${pr.repo.split("/")[0]}/${pr.repo.split("/")[1]}`}
          className="text-sm hover:underline"
          style={{ color: "var(--text-muted)" }}>
          ← Back to {pr.repo}
        </Link>
      </div>
    </div>
  );
}
