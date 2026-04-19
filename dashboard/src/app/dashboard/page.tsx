export const dynamic = "force-dynamic";
import { api } from "@/lib/api";
import { formatDistanceToNow } from "date-fns";
import { CheckCircle, GitPullRequest, Package, TrendingUp, XCircle, AlertTriangle } from "lucide-react";
import Link from "next/link";
import Badge, { classificationVariant, stateVariant } from "@/components/Badge";

export default async function DashboardPage() {
  const [stats, repos, devs] = await Promise.all([
    api.stats(),
    api.repos(),
    api.devs(),
  ]);

  // Get recent PRs across all repos (top 3 repos' first page)
  const recentPRsRaw = await Promise.all(
    repos.slice(0, 3).map((r) => api.repoPRs(r.owner, r.name, 1))
  );
  const recentPRs = recentPRsRaw.flatMap((p) => p.items).sort(
    (a, b) => new Date(b.opened_at).getTime() - new Date(a.opened_at).getTime()
  ).slice(0, 8);

  return (
    <div className="p-8 space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold" style={{ color: "var(--text)" }}>Overview</h1>
        <p className="text-sm mt-1" style={{ color: "var(--text-muted)" }}>
          AI-powered code review activity across all connected repos
        </p>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard icon={<Package size={20} />} label="Repos" value={stats.total_repos} />
        <StatCard icon={<GitPullRequest size={20} />} label="Total PRs" value={stats.total_prs} sub={`${stats.prs_last_7_days} this week`} />
        <StatCard icon={<TrendingUp size={20} />} label="Approval Rate" value={`${stats.approval_rate}%`} accent />
        <StatCard icon={<AlertTriangle size={20} />} label="Critical Issues" value={stats.total_critical_issues} danger={stats.total_critical_issues > 0} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Recent PRs */}
        <div className="lg:col-span-2 rounded-xl border p-5 space-y-1"
          style={{ background: "var(--surface)", borderColor: "var(--border)" }}>
          <h2 className="text-sm font-semibold mb-4" style={{ color: "var(--text)" }}>Recent Pull Requests</h2>
          {recentPRs.length === 0 && (
            <p className="text-sm py-6 text-center" style={{ color: "var(--text-muted)" }}>
              No PRs yet. Open a pull request in a connected repo.
            </p>
          )}
          {recentPRs.map((pr) => (
            <div key={pr.id}
              className="flex items-center justify-between py-3 border-b last:border-0"
              style={{ borderColor: "var(--border)" }}>
              <div className="flex items-center gap-3 min-w-0">
                {pr.review ? (
                  pr.review.approved
                    ? <CheckCircle size={16} style={{ color: "var(--success)", flexShrink: 0 }} />
                    : <XCircle size={16} style={{ color: "var(--danger)", flexShrink: 0 }} />
                ) : (
                  <div className="w-4 h-4 rounded-full border-2 border-dashed flex-shrink-0"
                    style={{ borderColor: "var(--text-muted)" }} />
                )}
                <div className="min-w-0">
                  <p className="text-sm font-medium truncate" style={{ color: "var(--text)" }}>
                    {pr.title || `PR #${pr.number}`}
                  </p>
                  <p className="text-xs truncate" style={{ color: "var(--text-muted)" }}>
                    @{pr.author} · {formatDistanceToNow(new Date(pr.opened_at), { addSuffix: true })}
                    {pr.plane_issue_id && <span className="ml-1 text-indigo-400">· #{pr.plane_issue_id}</span>}
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
                      View →
                    </Link>
                  </>
                )}
                {!pr.review && (
                  <span className="text-xs" style={{ color: "var(--text-muted)" }}>Pending</span>
                )}
              </div>
            </div>
          ))}
        </div>

        {/* Top developers */}
        <div className="rounded-xl border p-5"
          style={{ background: "var(--surface)", borderColor: "var(--border)" }}>
          <h2 className="text-sm font-semibold mb-4" style={{ color: "var(--text)" }}>Top Developers</h2>
          {devs.slice(0, 6).map((dev) => (
            <Link key={dev.author} href={`/devs/${dev.author}`}
              className="flex items-center justify-between py-2.5 border-b last:border-0 hover:opacity-80 transition-opacity"
              style={{ borderColor: "var(--border)" }}>
              <div>
                <p className="text-sm font-medium" style={{ color: "var(--text)" }}>@{dev.author}</p>
                <p className="text-xs" style={{ color: "var(--text-muted)" }}>{dev.total_prs} PRs</p>
              </div>
              <div className="text-right">
                <p className="text-sm font-semibold"
                  style={{ color: dev.approval_rate >= 80 ? "var(--success)" : dev.approval_rate >= 50 ? "var(--warning)" : "var(--danger)" }}>
                  {dev.approval_rate}%
                </p>
                <p className="text-xs" style={{ color: "var(--text-muted)" }}>approval</p>
              </div>
            </Link>
          ))}
          {devs.length === 0 && (
            <p className="text-sm text-center py-4" style={{ color: "var(--text-muted)" }}>No data yet</p>
          )}
        </div>
      </div>
    </div>
  );
}

function StatCard({
  icon, label, value, sub, accent, danger,
}: {
  icon: React.ReactNode;
  label: string;
  value: string | number;
  sub?: string;
  accent?: boolean;
  danger?: boolean;
}) {
  const color = danger ? "var(--danger)" : accent ? "var(--accent-hover)" : "var(--text)";
  return (
    <div className="rounded-xl border p-5"
      style={{ background: "var(--surface)", borderColor: "var(--border)" }}>
      <div className="flex items-center gap-2 mb-3" style={{ color: "var(--text-muted)" }}>
        {icon}
        <span className="text-xs font-medium uppercase tracking-wide">{label}</span>
      </div>
      <p className="text-2xl font-bold" style={{ color }}>{value}</p>
      {sub && <p className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>{sub}</p>}
    </div>
  );
}
