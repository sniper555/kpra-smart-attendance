"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, ApiError, Me, setCsrf } from "@/lib/api";

type Item = { href: string; label: string; match: string };

export function Shell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");

  useEffect(() => {
    api<Me>("/api/auth/me")
      .then((data) => {
        setCsrf(data.csrf_token);
        setMe(data);
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError && (err.status === 401 || err.code === "SETUP_INCOMPLETE")) {
          router.replace(err.code === "SETUP_INCOMPLETE" ? "/continue" : "/login");
          return;
        }
        setError(err instanceof Error ? err.message : "Could not load your session.");
      });
  }, [router]);

  useEffect(() => {
    setSearch(window.location.search);
  }, [pathname]);

  async function signOut() {
    await api("/api/auth/logout", { method: "POST" });
    setCsrf(null);
    router.replace("/login");
  }

  if (!me) {
    return (
      <main id="main" className="mx-auto max-w-lg px-4 py-16 text-center text-navy-800">
        {error || "Loading your session…"}
      </main>
    );
  }

  const admin = me.user.role === "ADMIN";
  const items: Item[] = [
    { href: "/home", label: "Dashboard", match: "/home" },
    { href: "/history", label: "History & Reports", match: "/history" },
    { href: "/corrections", label: "Corrections", match: "/corrections" },
  ];
  if (admin) {
    items.push(
      { href: "/admin", label: "Administration", match: "/admin" },
      { href: "/admin?tab=policy", label: "Office timings", match: "/admin" },
      { href: "/admin?tab=devices", label: "Device Integration", match: "/admin" },
      { href: "/admin?tab=timenex", label: "TimeNex mapping", match: "/admin" },
    );
    if (me.environment === "development") {
      items.push({ href: "/admin?tab=qa", label: "Test Harness", match: "/admin" });
    }
  }
  items.push({ href: "/account", label: "Settings", match: "/account" });
  const roleLabel = admin ? "Administrator" : "Authorized Manager";
  const name = me.user.employee?.full_name || me.user.display_name;

  return (
    <div className="min-h-screen bg-[#eef3f8] lg:grid lg:grid-cols-[250px_1fr]">
      <aside className="hidden bg-[#071a33] text-white lg:flex lg:min-h-screen lg:flex-col">
        <div className="border-b border-white/10 px-5 py-5">
          <p className="text-lg font-semibold tracking-wide">KPRA</p>
          <p className="text-xs text-slate-300">Khyber Pakhtunkhwa Revenue Authority</p>
        </div>
        <nav className="flex flex-1 flex-col gap-1 px-3 py-4">
          {items.map((item) => (
            <Link
              key={item.label}
              href={item.href}
              className={`rounded-xl px-3 py-2.5 text-sm font-medium ${
                navActive(pathname, search, item) ? "bg-white text-[#071a33]" : "text-slate-200 hover:bg-white/10"
              }`}
            >
              {item.label}
            </Link>
          ))}
        </nav>
        <button className="m-3 rounded-xl px-3 py-2.5 text-left text-sm font-medium text-slate-200 hover:bg-white/10" type="button" onClick={signOut}>
          Logout
        </button>
      </aside>
      <div className="min-w-0">
        <header className="flex items-center justify-between gap-3 border-b border-slate-200 bg-white px-4 py-3">
          <div>
            <p className="text-lg font-semibold text-[#071a33]">KPRA Smart Attendance</p>
            <p className="text-xs text-slate-500">Secure · Simple · Reliable</p>
          </div>
          <div className="text-right">
            <p className="text-sm font-semibold text-[#071a33]">{name}</p>
            <p className="text-xs text-slate-500">{roleLabel}</p>
          </div>
        </header>
        {me.environment !== "production" && (
          <div className="bg-amber-100 px-4 py-2 text-center text-sm text-amber-950">
            {me.environment.toUpperCase()} environment. Records here are not official KPRA production attendance.
          </div>
        )}
        <main id="main" className="px-4 py-5 pb-24 lg:pb-6">
          {children}
        </main>
        <nav className="fixed inset-x-0 bottom-0 z-20 grid grid-cols-4 border-t border-slate-200 bg-white pb-[env(safe-area-inset-bottom)] lg:hidden">
          {(admin
            ? [
                ["/home", "Home"],
                ["/history", "History"],
                ["/admin?tab=mark", "Mark"],
                ["/account", "Profile"],
              ]
            : [
                ["/home", "Home"],
                ["/history", "History"],
                ["/corrections", "Corrections"],
                ["/account", "Profile"],
              ]
          ).map(([href, label]) => (
            <Link
              key={label}
              href={href}
              className={`flex min-h-14 items-center justify-center px-2 text-center text-sm font-semibold ${pathname === href.split("?")[0] && (href.includes("tab=mark") ? search.includes("tab=mark") : !search.includes("tab=")) ? "text-[#1746d1]" : "text-slate-500"}`}
            >
              {label}
            </Link>
          ))}
        </nav>
      </div>
    </div>
  );
}

function navActive(pathname: string, search: string, item: Item) {
  if (item.href.includes("?")) return `${pathname}${search}` === item.href;
  if (pathname === "/admin" && search.includes("tab=")) return item.href === "/admin" ? false : pathname === item.match;
  return pathname === item.match;
}
