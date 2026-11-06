import type { Metadata, Viewport } from "next";
import "./globals.css";
import { InstallBanner } from "@/components/InstallBanner";
import { ServiceWorker } from "@/components/ServiceWorker";

export const metadata: Metadata = {
  title: "KPRA Smart Attendance Lite",
  description: "Remote attendance for authorized KPRA managers.",
  robots: { index: false, follow: false },
  manifest: "/manifest.webmanifest",
  appleWebApp: { capable: true, title: "KPRA Attendance", statusBarStyle: "default" },
};

export const viewport: Viewport = {
  themeColor: "#0B1F3A",
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:bg-white focus:px-3 focus:py-2">
          Skip to content
        </a>
        <ServiceWorker />
        <InstallBanner />
        {children}
      </body>
    </html>
  );
}
