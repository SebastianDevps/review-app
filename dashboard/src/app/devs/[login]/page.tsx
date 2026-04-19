import { api } from "@/lib/api";
import { formatDistanceToNow } from "date-fns";
import { CheckCircle, XCircle, Minus, FileCode } from "lucide-react";
import Link from "next/link";
import Badge, { classificationVariant, stateVariant } from "@/components/Badge";
import { notFound } from "next/navigation";

export default async function DevDetailPage({
  params,
}: {
  params: Promise<{ login: string }>;
}) {
  const { login } = await params;
  let dev;
  try {
    dev = await api.dev(login);
  } catch {
    notFound();
  }

  const rateColor =
    dev.approval_rate >= 80
      ? "var(--success)"
      : dev.approval_rate >= 50
        ? "var(--warning)"
        : "var(--danger)";

  return (
    <div className="p-8 max-w-5xl space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold" style={{ color: "var(--text)" }}>@{dev.author}</h1>
        <p className="text-sm mt-1" style={{ color: "var(--text-muted)" }}>
          Developer metrics across all connected repositories
        </p>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Total PRs" value={dev.total_prs} />
        <StatCard label="Reviewed" value={dev.reviewed_prs} />
        <StatCard label="Approval Rate" value={`${dev.approval_rate}%`} color={rateColor} />
        <StatCard
          label="Critical Issues"
          value={dev.critical_issues}
          color={dev.critical_issues > 0 ? "var(--danger)" : undefined}
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Recent PRs */}
        <div className="lg:col-span-2 rounded-xl border p-5"
          style={{ background: "var(--surface)", borderColor: "var(--border)" }}>
          <h2 className="text-sm font-semibold mb-4" style={{ color: "var(--text)" }}>Recent PRs</h2>
          <div className="space-y-0">
            {dev.recent_prs.map((pr) => (
              <div key={pr.id}
                className="flex items-center justify-between py-3 border-b last:border-0"
                style={{ borderColor: "var(--border)" }}>
                <div className="flex items-center gap-3 min-w-0">
                  {pr.review ? (
                    pr.review.approved
                      ? <CheckCircle size={14} style={{ color: "var(--success)", flexShrink: 0 }} />
                      : <XCircle size={14} style={{ color: "var(--danger)", flexShrink: 0 }} />
                  ) : (
                    <Minus size={14} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
                  )}
                  <div className="min-w-0">
                    <p className="text-sm font-medium truncate" style={{ color: "var(--text)" }}>
                      {pr.title || `PR #${pr.number}`}
                    </p>
                    <p className="text-xs" style={{ color: "var(--text-muted)" }}>
                      {formatDistanceToNow(new Date(pr.opened_at), { addSuffix: true })}
                      {pr.plane_issue_id && <span className="text-indigo-400 ml-1">· #{pr.plane_issue_id}</span>}
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-2 flex-shrink-0 ml-3">
                  {pr.review && (
                    <>
                      <Badge variant={classificationVariant(pr.review.classification)}>
                        {pr.review.classification}
                      </Badge>
                      <Badge variant={stateVariant(pr.review.plane_state)}>
                        {pr.review.plane_state.replace("_", " ")}
                      </Badge>
                      <Link href={`/reviews/${pr.review.id}`}
                        className="text-xs hover:underline"
                        style={{ color: "var(--accent-hover)" }}>
                        →
                      </Link>
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Top issue files */}
        <div className="rounded-xl border p-5"
          style={{ background: "var(--surface)", borderColor: "var(--border)" }}>
          <h2 className="text-sm font-semibold mb-4" style={{ color: "var(--text)" }}>
            Files with Most Issues
          </h2>
          {dev.top_issue_files.length === 0 && (
            <p className="text-sm" style={{ color: "var(--text-muted)" }}>No issues recorded yet.</p>
          )}
          {dev.top_issue_files.map(({ file, count }) => (
            <div key={file}
              className="flex items-center justify-between py-2.5 border-b last:border-0"
              style={{ borderColor: "var(--border)" }}>
              <span className="flex items-center gap-2 text-xs truncate" style={{ color: "var(--text-muted)" }}>
                <FileCode size={12} style={{ flexShrink: 0 }} />
                <span className="truncate">{file}</span>
              </span>
              <span className="text-sm font-semibold ml-2" style={{ color: "var(--danger)", flexShrink: 0 }}>
                {count}
              </span>
            </div>
          ))}
        </div>
      </div>

      <Link href="/devs" className="text-sm hover:underline" style={{ color: "var(--text-muted)" }}>
        ← Back to Developers
      </Link>
    </div>
  );
}

function StatCard({ label, value, color }: { label: string; value: string | number; color?: string }) {
  return (
    <div className="rounded-xl border p-5"
      style={{ background: "var(--surface)", borderColor: "var(--border)" }}>
      <p className="text-xs uppercase tracking-wide mb-2" style={{ color: "var(--text-muted)" }}>{label}</p>
      <p className="text-2xl font-bold" style={{ color: color || "var(--text)" }}>{value}</p>
    </div>
  );
}
