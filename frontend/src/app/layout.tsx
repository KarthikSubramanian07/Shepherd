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
          <div className="flex h-screen overflow-hidden">
            <Sidebar />
            <main className="flex-1 overflow-auto">{children}</main>
          </div>
        </ShepherdProvider>
      </body>
    </html>
  );
}
