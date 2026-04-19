/**
 * API client — thin wrapper around fetch to the FastAPI backend.
 * All calls go through /api/backend/* which Next.js rewrites to http://localhost:8000/*
 */

const BASE = "/api/backend";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    next: { revalidate: 30 }, // 30s ISR cache
  });
  if (!res.ok) {
    throw new Error(`API error ${res.status}: ${path}`);
  }
  return res.json();
}

// ── Types ─────────────────────────────────────────────────────────────────────

export interface Stats {
  total_repos: number;
  total_prs: number;
  total_reviews: number;
  approval_rate: number;
  prs_last_7_days: number;
  total_critical_issues: number;
  total_high_issues: number;
}

export interface Repo {
  id: number;
  full_name: string;
  owner: string;
  name: string;
  default_branch: string;
  indexed_at: string | null;
  chunk_count: number;
  index_status: "indexed" | "pending";
  total_prs: number;
  approval_rate: number;
  created_at: string;
}

export interface ReviewSummary {
  id: number;
  classification: string;
  approved: boolean;
  plane_state: string;
  total_issues: number;
  critical_issues: number;
  reviewed_at: string;
}

export interface PR {
  id: number;
  number: number;
  title: string;
  author: string;
  branch: string;
  plane_issue_id: string | null;
  additions: number;
  deletions: number;
  opened_at: string;
  review: ReviewSummary | null;
}

export interface ReviewDetail {
  id: number;
  classification: string;
  approved: boolean;
  plane_state: string;
  summary: string;
  suggestion: string;
  reviewed_at: string;
  total_issues: number;
  issues_by_severity: {
    critical: number;
    high: number;
    medium: number;
    low: number;
  };
  issues: {
    severity: string;
    file: string;
    line: number | null;
    comment: string;
  }[];
  pull_request: {
    number: number;
    title: string;
    author: string;
    branch: string;
    plane_issue_id: string | null;
    additions: number;
    deletions: number;
    repo: string;
  };
}

export interface Dev {
  author: string;
  total_prs: number;
  reviewed_prs: number;
  approved_prs: number;
  approval_rate: number;
  total_issues: number;
  critical_issues: number;
  last_pr_at: string | null;
}

export interface DevDetail extends Dev {
  top_issue_files: { file: string; count: number }[];
  recent_prs: PR[];
}

export interface PaginatedPRs {
  total: number;
  page: number;
  per_page: number;
  pages: number;
  items: PR[];
}

// ── API calls ─────────────────────────────────────────────────────────────────

export const api = {
  stats: () => get<Stats>("/api/stats"),
  repos: () => get<Repo[]>("/api/repos"),
  repoPRs: (owner: string, repo: string, page = 1) =>
    get<PaginatedPRs>(`/api/repos/${owner}/${repo}/prs?page=${page}`),
  review: (id: number) => get<ReviewDetail>(`/api/reviews/${id}`),
  devs: () => get<Dev[]>("/api/devs"),
  dev: (login: string) => get<DevDetail>(`/api/devs/${login}`),
  triggerIndex: (installationId: number, owner: string, repo: string) =>
    fetch(`${BASE}/index/${installationId}/${owner}/${repo}`, { method: "POST" }).then((r) => r.json()),
};
