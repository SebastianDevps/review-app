export const dynamic = "force-dynamic";
import { api } from "@/lib/api";
import { formatDistanceToNow } from "date-fns";
import { TrendingUp, TrendingDown, Minus } from "lucide-react";
import Link from "next/link";

export default async function DevsPage() {
  const devs = await api.devs();

  return (
    <div className="p-8 space-y-6">
      <div>
        <h1 className="text-2xl font-bold" style={{ color: "var(--text)" }}>Developers</h1>
        <p className="text-sm mt-1" style={{ color: "var(--text-muted)" }}>
          Code quality metrics per developer across all connected repos
        </p>
      </div>

      {devs.length === 0 && (
        <div className="rounded-xl border p-12 text-center"
          style={{ background: "var(--surface)", borderColor: "var(--border)" }}>
          <p className="font-medium" style={{ color: "var(--text)" }}>No developer data yet</p>
          <p className="text-sm mt-2" style={{ color: "var(--text-muted)" }}>
            Data will appear once pull requests are reviewed.
          </p>
        </div>
      )}

      <div className="rounded-xl border overflow-hidden"
        style={{ background: "var(--surface)", borderColor: "var(--border)" }}>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-xs uppercase tracking-wide"
              style={{ borderColor: "var(--border)", color: "var(--text-muted)" }}>
              <th className="px-5 py-3 text-left">Developer</th>
              <th className="px-4 py-3 text-right">PRs</th>
              <th className="px-4 py-3 text-right">Reviewed</th>
              <th className="px-4 py-3 text-right">Approval Rate</th>
              <th className="px-4 py-3 text-right">Issues</th>
              <th className="px-4 py-3 text-right">Critical</th>
              <th className="px-4 py-3 text-right">Last PR</th>
              <th className="px-4 py-3"></th>
            </tr>
          </thead>
          <tbody>
            {devs.map((dev) => {
              const rateColor =
                dev.approval_rate >= 80
                  ? "var(--success)"
                  : dev.approval_rate >= 50
                    ? "var(--warning)"
                    : "var(--danger)";
              const RateIcon =
                dev.approval_rate >= 80
                  ? TrendingUp
                  : dev.approval_rate >= 50
                    ? Minus
                    : TrendingDown;

              return (
                <tr key={dev.author}
                  className="border-b last:border-0 hover:bg-white/[0.02] transition-colors"
                  style={{ borderColor: "var(--border)" }}>
                  <td className="px-5 py-3">
                    <Link href={`/devs/${dev.author}`}
                      className="font-medium hover:underline"
                      style={{ color: "var(--accent-hover)" }}>
                      @{dev.author}
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-right" style={{ color: "var(--text)" }}>
                    {dev.total_prs}
                  </td>
                  <td className="px-4 py-3 text-right" style={{ color: "var(--text-muted)" }}>
                    {dev.reviewed_prs}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <span className="flex items-center justify-end gap-1 font-semibold" style={{ color: rateColor }}>
                      <RateIcon size={13} />
                      {dev.approval_rate}%
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right" style={{ color: "var(--text-muted)" }}>
                    {dev.total_issues}
                  </td>
                  <td className="px-4 py-3 text-right"
                    style={{ color: dev.critical_issues > 0 ? "var(--danger)" : "var(--text-muted)" }}>
                    {dev.critical_issues}
                  </td>
                  <td className="px-4 py-3 text-right text-xs" style={{ color: "var(--text-muted)" }}>
                    {dev.last_pr_at
                      ? formatDistanceToNow(new Date(dev.last_pr_at), { addSuffix: true })
                      : "—"}
                  </td>
                  <td className="px-4 py-3">
                    <Link href={`/devs/${dev.author}`}
                      className="text-xs hover:underline"
                      style={{ color: "var(--accent-hover)" }}>
                      Detail →
                    </Link>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
