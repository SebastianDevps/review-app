import { api } from "@/lib/api";
import { formatDistanceToNow } from "date-fns";
import { CheckCircle, Clock, Database, ExternalLink, GitBranch, RefreshCw } from "lucide-react";
import Link from "next/link";
import Badge from "@/components/Badge";

export default async function ReposPage() {
  const repos = await api.repos();

  return (
    <div className="p-8 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold" style={{ color: "var(--text)" }}>Repositories</h1>
          <p className="text-sm mt-1" style={{ color: "var(--text-muted)" }}>
            Connected repos — install the GitHub App to add more
          </p>
        </div>
        <a
          href="https://github.com/apps/your-review-app/installations/new"
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors"
          style={{ background: "var(--accent)", color: "#fff" }}>
          <ExternalLink size={14} />
          Install GitHub App
        </a>
      </div>

      {repos.length === 0 && (
        <div className="rounded-xl border p-12 text-center"
          style={{ background: "var(--surface)", borderColor: "var(--border)" }}>
          <Database size={40} className="mx-auto mb-4" style={{ color: "var(--text-muted)" }} />
          <p className="font-medium" style={{ color: "var(--text)" }}>No repositories connected</p>
          <p className="text-sm mt-2" style={{ color: "var(--text-muted)" }}>
            Install the GitHub App on a repo to start getting AI code reviews.
          </p>
        </div>
      )}

      <div className="grid grid-cols-1 gap-4">
        {repos.map((repo) => (
          <div key={repo.id} className="rounded-xl border p-5"
            style={{ background: "var(--surface)", borderColor: "var(--border)" }}>
            <div className="flex items-start justify-between">
              <div className="space-y-1">
                <div className="flex items-center gap-2">
                  <Link href={`/repos/${repo.owner}/${repo.name}`}
                    className="text-base font-semibold hover:underline"
                    style={{ color: "var(--accent-hover)" }}>
                    {repo.full_name}
                  </Link>
                  <Badge variant={repo.index_status === "indexed" ? "success" : "muted"}>
                    {repo.index_status === "indexed" ? "Indexed" : "Pending index"}
                  </Badge>
                </div>
                <div className="flex items-center gap-4 text-xs" style={{ color: "var(--text-muted)" }}>
                  <span className="flex items-center gap-1">
                    <GitBranch size={12} />
                    {repo.default_branch}
                  </span>
                  <span className="flex items-center gap-1">
                    <Database size={12} />
                    {repo.chunk_count.toLocaleString()} chunks
                  </span>
                  {repo.indexed_at && (
                    <span className="flex items-center gap-1">
                      <Clock size={12} />
                      Indexed {formatDistanceToNow(new Date(repo.indexed_at), { addSuffix: true })}
                    </span>
                  )}
                </div>
              </div>

              {/* Stats */}
              <div className="flex items-center gap-6 text-right">
                <div>
                  <p className="text-xl font-bold" style={{ color: "var(--text)" }}>{repo.total_prs}</p>
                  <p className="text-xs" style={{ color: "var(--text-muted)" }}>Total PRs</p>
                </div>
                <div>
                  <p className="text-xl font-bold"
                    style={{ color: repo.approval_rate >= 80 ? "var(--success)" : repo.approval_rate >= 50 ? "var(--warning)" : "var(--danger)" }}>
                    {repo.approval_rate}%
                  </p>
                  <p className="text-xs" style={{ color: "var(--text-muted)" }}>Approval</p>
                </div>
                <Link href={`/repos/${repo.owner}/${repo.name}`}
                  className="px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors hover:opacity-80"
                  style={{ borderColor: "var(--border)", color: "var(--text-muted)" }}>
                  View PRs →
                </Link>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
