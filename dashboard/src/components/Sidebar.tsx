"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { BarChart3, Bot, Code2, GitPullRequest, LayoutDashboard, Users } from "lucide-react";
import clsx from "clsx";

const nav = [
  { href: "/dashboard", label: "Overview", icon: LayoutDashboard },
  { href: "/repos", label: "Repositories", icon: Code2 },
  { href: "/devs", label: "Developers", icon: Users },
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="fixed left-0 top-0 h-screen w-56 flex flex-col border-r"
      style={{ background: "var(--surface)", borderColor: "var(--border)" }}>

      {/* Logo */}
      <div className="flex items-center gap-2 px-5 py-5 border-b" style={{ borderColor: "var(--border)" }}>
        <div className="w-8 h-8 rounded-lg flex items-center justify-center"
          style={{ background: "var(--accent)" }}>
          <Bot size={18} className="text-white" />
        </div>
        <div>
          <p className="text-sm font-semibold" style={{ color: "var(--text)" }}>Review App</p>
          <p className="text-xs" style={{ color: "var(--text-muted)" }}>AI Code Review</p>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 py-4 space-y-1">
        {nav.map(({ href, label, icon: Icon }) => {
          const active = pathname === href || pathname.startsWith(href + "/");
          return (
            <Link key={href} href={href}
              className={clsx(
                "flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors",
                active
                  ? "font-medium"
                  : "hover:bg-white/5"
              )}
              style={{
                color: active ? "var(--accent-hover)" : "var(--text-muted)",
                background: active ? "rgba(99,102,241,0.12)" : undefined,
              }}>
              <Icon size={16} />
              {label}
            </Link>
          );
        })}
      </nav>

      {/* Footer */}
      <div className="px-4 py-4 border-t text-xs" style={{ borderColor: "var(--border)", color: "var(--text-muted)" }}>
        <p>v0.3.0 · MIT License</p>
      </div>
    </aside>
  );
}
