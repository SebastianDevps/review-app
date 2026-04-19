import type { Metadata } from "next";
import "./globals.css";
import Sidebar from "@/components/Sidebar";

export const metadata: Metadata = {
  title: "Review App — AI Code Review",
  description: "Automated AI code reviews with GitHub + Plane integration",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="flex min-h-screen">
          <Sidebar />
          <main className="flex-1 ml-56 min-h-screen" style={{ background: "var(--background)" }}>
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}
