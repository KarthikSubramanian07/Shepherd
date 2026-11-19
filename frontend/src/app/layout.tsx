import type { Metadata } from "next";
import "./globals.css";
import { Sidebar } from "@/components/layout/Sidebar";
import { ShepherdProvider } from "@/lib/shepherd-ws";

export const metadata: Metadata = {
  title: "Shepherd · Agent Command Center",
  description:
    "Record a task once, let agents run it, and watch them traverse the routine graph with a proactive safety layer.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <ShepherdProvider>
          <a href="#main-content" className="skip-link">
            Skip to main content
          </a>
          <div className="flex h-screen overflow-hidden">
            <Sidebar />
            <main
              id="main-content"
              tabIndex={0}
              aria-label="Main content"
              className="flex-1 overflow-auto outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent/50"
            >
              {children}
            </main>
          </div>
        </ShepherdProvider>
      </body>
    </html>
  );
}
