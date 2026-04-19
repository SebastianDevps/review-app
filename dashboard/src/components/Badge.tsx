import clsx from "clsx";

type Variant = "success" | "danger" | "warning" | "info" | "muted";

const styles: Record<Variant, { bg: string; color: string }> = {
  success: { bg: "rgba(34,197,94,0.12)", color: "#22c55e" },
  danger:  { bg: "rgba(239,68,68,0.12)",  color: "#ef4444" },
  warning: { bg: "rgba(245,158,11,0.12)", color: "#f59e0b" },
  info:    { bg: "rgba(99,102,241,0.12)", color: "#818cf8" },
  muted:   { bg: "rgba(136,136,168,0.1)", color: "#8888a8" },
};

export default function Badge({
  children,
  variant = "muted",
  className,
}: {
  children: React.ReactNode;
  variant?: Variant;
  className?: string;
}) {
  const { bg, color } = styles[variant];
  return (
    <span
      className={clsx("inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium", className)}
      style={{ background: bg, color }}
    >
      {children}
    </span>
  );
}

export function severityVariant(s: string): Variant {
  if (s === "critical") return "danger";
  if (s === "high") return "warning";
  if (s === "medium") return "info";
  return "muted";
}

export function classificationVariant(c: string): Variant {
  if (c === "complex") return "warning";
  if (c === "moderate") return "info";
  return "muted";
}

export function stateVariant(s: string): Variant {
  if (s === "qa_testing") return "success";
  if (s === "refused") return "danger";
  return "info";
}
