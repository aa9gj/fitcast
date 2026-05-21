import type { Metadata, Viewport } from "next";
import Link from "next/link";
import "./globals.css";
import ServiceWorkerRegister from "@/components/ServiceWorkerRegister";

const REPO = "https://github.com/aa9gj/fitcast";

export const metadata: Metadata = {
  title: "fitcast — forecast your fit for jobs",
  description:
    "A case study in shipping real AI tooling: scrape job boards, have Claude extract requirements, and derive an auditable fit score. Try the live demo with your own resume.",
  applicationName: "fitcast",
  manifest: "/manifest.webmanifest",
  appleWebApp: { capable: true, title: "fitcast", statusBarStyle: "black-translucent" },
  icons: {
    icon: [{ url: "/icon.svg", type: "image/svg+xml" }],
    apple: [{ url: "/icon.svg" }],
  },
  openGraph: {
    title: "fitcast — forecast your fit for jobs",
    description:
      "Scrape job boards, have Claude extract requirements, derive an auditable fit score. Live demo + case study.",
    type: "website",
  },
};

export const viewport: Viewport = {
  themeColor: "#0d1117",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen flex flex-col antialiased">
        <header className="sticky top-0 z-40 border-b border-ink-line bg-ink/80 backdrop-blur">
          <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-5">
            <Link href="/" className="flex items-center gap-2 font-mono text-sm font-semibold">
              <span className="inline-block h-2.5 w-2.5 rounded-full bg-accent" />
              fitcast
            </Link>
            <nav className="flex items-center gap-5 text-sm text-zinc-400">
              <Link href="/#how" className="hidden hover:text-zinc-100 sm:inline">
                How it works
              </Link>
              <Link href="/#scoring" className="hidden hover:text-zinc-100 sm:inline">
                Scoring
              </Link>
              <a
                href={REPO}
                target="_blank"
                rel="noopener noreferrer"
                className="hover:text-zinc-100"
              >
                GitHub
              </a>
              <Link
                href="/demo"
                className="rounded-md bg-accent px-3 py-1.5 font-medium text-white hover:bg-accent-soft"
              >
                Live demo
              </Link>
            </nav>
          </div>
        </header>

        <main className="flex-1">{children}</main>

        <footer className="border-t border-ink-line">
          <div className="mx-auto max-w-6xl px-5 py-10 text-sm text-zinc-500">
            <div className="flex flex-col gap-6 sm:flex-row sm:items-start sm:justify-between">
              <div className="max-w-md">
                <div className="font-mono text-zinc-300">fitcast</div>
                <p className="mt-2 leading-relaxed">
                  A portfolio case study: a real Python CLI that scrapes job boards and
                  uses Claude for structured extraction, plus this web app that runs the
                  same scoring logic live in your browser.
                </p>
              </div>
              <div className="flex gap-12">
                <div className="space-y-2">
                  <div className="font-medium text-zinc-300">Project</div>
                  <a href={REPO} className="block hover:text-zinc-200" target="_blank" rel="noreferrer">
                    Source (GitHub)
                  </a>
                  <a
                    href="https://colab.research.google.com/github/aa9gj/fitcast/blob/main/fitcast.ipynb"
                    className="block hover:text-zinc-200"
                    target="_blank"
                    rel="noreferrer"
                  >
                    Run in Colab
                  </a>
                  <Link href="/demo" className="block hover:text-zinc-200">
                    Live demo
                  </Link>
                </div>
                <div className="space-y-2">
                  <div className="font-medium text-zinc-300">License</div>
                  <p className="max-w-[14rem] leading-relaxed">
                    PolyForm Noncommercial 1.0.0 — source-available, free for
                    noncommercial use.
                  </p>
                </div>
              </div>
            </div>
            <div className="mt-8 border-t border-ink-line pt-6 text-xs text-zinc-600">
              Not affiliated with any job board. The demo never stores your resume or
              API key.
            </div>
          </div>
        </footer>

        <ServiceWorkerRegister />
      </body>
    </html>
  );
}
