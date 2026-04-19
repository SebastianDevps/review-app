export const dynamic = "force-dynamic";
import { api } from "@/lib/api";
import { formatDistanceToNow } from "date-fns";
import { CheckCircle, XCircle, Minus } from "lucide-react";
import Link from "next/link";
import Badge, { classificationVariant, stateVariant } from "@/components/Badge";

export default async function RepoDetailPage({
  params,
}: {
  params: Promise<{ owner: string; repo: string }>;
}) {
  const { owner, repo } = await params;
  const data = await api.repoPRs(owner, repo, 1);

  return (
    <div className="p-8 space-y-6">
      <div>
        <h1 className="text-2xl font-bold" style={{ color: "var(--text)" }}>
          {owner}/{repo}
        </h1>
        <p className="text-sm mt-1" style={{ color: "var(--text-muted)" }}>
          {data.total} pull requests · Page {data.page} of {data.pages}
        </p>
      </div>

      {/* PR list */}
      <div className="rounded-xl border overflow-hidden"
        style={{ background: "var(--surface)", borderColor: "var(--border)" }}>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-xs uppercase tracking-wide"
              style={{ borderColor: "var(--border)", color: "var(--text-muted)" }}>
              <th className="px-5 py-3 text-left">Pull Request</th>
              <th className="px-4 py-3 text-left">Author</th>
              <th className="px-4 py-3 text-left">Classification</th>
              <th className="px-4 py-3 text-left">State</th>
              <th className="px-4 py-3 text-left">Issues</th>
              <th className="px-4 py-3 text-left">Opened</th>
              <th className="px-4 py-3"></th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((pr) => (
              <tr key={pr.id} className="border-b last:border-0 hover:bg-white/[0.02] transition-colors"
                style={{ borderColor: "var(--border)" }}>
                <td className="px-5 py-3">
                  <div className="flex items-center gap-2">
                    {pr.review ? (
                      pr.review.approved
                        ? <CheckCircle size={14} style={{ color: "var(--success)", flexShrink: 0 }} />
                        : <XCircle size={14} style={{ color: "var(--danger)", flexShrink: 0 }} />
                    ) : (
                      <Minus size={14} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
                    )}
                    <div className="min-w-0">
                      <p className="font-medium truncate max-w-xs" style={{ color: "var(--text)" }}>
                        {pr.title || `PR #${pr.number}`}
                      </p>
                      <p className="text-xs truncate" style={{ color: "var(--text-muted)" }}>
                        #{pr.number} · {pr.branch}
                        {pr.plane_issue_id && <span className="text-indigo-400 ml-1">· Plane #{pr.plane_issue_id}</span>}
                      </p>
                    </div>
                  </div>
                </td>
                <td className="px-4 py-3">
                  <span style={{ color: "var(--text-muted)" }}>@{pr.author}</span>
                </td>
                <td className="px-4 py-3">
                  {pr.review
                    ? <Badge variant={classificationVariant(pr.review.classification)}>{pr.review.classification}</Badge>
                    : <span style={{ color: "var(--text-muted)" }}>—</span>}
                </td>
                <td className="px-4 py-3">
                  {pr.review
                    ? <Badge variant={stateVariant(pr.review.plane_state)}>{pr.review.plane_state.replace("_", " ")}</Badge>
                    : <span style={{ color: "var(--text-muted)" }}>pending</span>}
                </td>
                <td className="px-4 py-3">
                  {pr.review ? (
                    <span style={{ color: pr.review.critical_issues > 0 ? "var(--danger)" : "var(--text-muted)" }}>
                      {pr.review.total_issues}
                      {pr.review.critical_issues > 0 && ` (${pr.review.critical_issues} critical)`}
                    </span>
                  ) : <span style={{ color: "var(--text-muted)" }}>—</span>}
                </td>
                <td className="px-4 py-3 text-xs" style={{ color: "var(--text-muted)" }}>
                  {formatDistanceToNow(new Date(pr.opened_at), { addSuffix: true })}
                </td>
                <td className="px-4 py-3">
                  {pr.review && (
                    <Link href={`/reviews/${pr.review.id}`}
                      className="text-xs hover:underline"
                      style={{ color: "var(--accent-hover)" }}>
                      Review →
                    </Link>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {data.items.length === 0 && (
          <p className="text-center py-10 text-sm" style={{ color: "var(--text-muted)" }}>
            No pull requests yet.
          </p>
        )}
      </div>
    </div>
  );
}
