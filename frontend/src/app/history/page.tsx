"use client";

import { FormEvent, useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { api, apiUrl, ApiError, AttendanceEvent, pktDateInput, setCsrf } from "@/lib/api";

export default function HistoryPage() {
  return <Shell><HistoryBody /></Shell>;
}

function HistoryBody() {
  const [from, setFrom] = useState(pktDateInput(30));
  const [to, setTo] = useState(pktDateInput(0));
  const [events, setEvents] = useState<AttendanceEvent[]>([]);
  const [error, setError] = useState("");

  async function load(event?: FormEvent) {
    event?.preventDefault();
    setError("");
    try {
      const data = await api<{ events: AttendanceEvent[] }>(
        `/api/attendance/me/history?date_from=${from}&date_to=${to}&limit=200`,
      );
      setEvents(data.events);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load history.");
    }
  }

  useEffect(() => {
    load().catch(() => undefined);
  }, []);

  function syncDay(id: string, confirm = false) {
    const path = `/api/attendance/sync-to-timenex/${id}${confirm ? "?confirm_overwrite=true" : ""}`;
    api<{ message: string; timenex_error?: string | null }>(path, { method: "POST" })
      .then((result) => {
        setError(result.timenex_error || "");
        return load();
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.code === "FORCE_REQUIRED" && !confirm) {
          const accepted = window.confirm(`Force sync this day\n\n${err.message}\n\nOverwrite TimeNex day with these times?`);
          if (accepted) syncDay(id, true);
          return;
        }
        setError(err instanceof ApiError ? err.message : "TimeNex did not accept the day.");
      });
  }

  const rows = groupDays(events);
  const presentDays = rows.filter((row) => row.checkIn !== "—").length;
  const missingOut = rows.filter((row) => row.checkIn !== "—" && row.checkOut === "—").length;
  const confirmed = events.filter((event) => event.event_type === "CHECK_IN" && event.timenex_confirmed).length;

  async function exportCsv() {
    setError("");
    try {
      const me = await api<{ csrf_token: string }>("/api/auth/me");
      setCsrf(me.csrf_token);
      const response = await fetch(apiUrl("/api/attendance/me/export"), {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": me.csrf_token },
        body: JSON.stringify({ date_from: from, date_to: to }),
      });
      if (!response.ok) {
        const data = await response.json();
        setError(data.error?.message || "Export failed.");
        return;
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "my-attendance.csv";
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Export failed.");
    }
  }

  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <article className="rounded-3xl bg-white p-4 shadow-sm"><p className="text-sm text-slate-500">Days with check-in</p><p className="text-2xl font-semibold">{presentDays}</p></article>
        <article className="rounded-3xl bg-white p-4 shadow-sm"><p className="text-sm text-slate-500">Missing check-out</p><p className="text-2xl font-semibold">{missingOut}</p></article>
        <article className="rounded-3xl bg-white p-4 shadow-sm"><p className="text-sm text-slate-500">Events</p><p className="text-2xl font-semibold">{events.length}</p></article>
        <article className="rounded-3xl bg-white p-4 shadow-sm"><p className="text-sm text-slate-500">Confirmed by TimeNex</p><p className="text-2xl font-semibold">{confirmed}</p><p className="text-xs text-slate-500">Check-ins in this date range</p></article>
      </section>
    <section className="rounded-3xl bg-white p-5 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Attendance Reports</h1>
          <p className="mt-1 text-sm text-slate-600">Your own records in this application. The TimeNex link is the current mapping. The TimeNex column says whether that punch was confirmed.</p>
        </div>
        <button className="btn-primary" type="button" onClick={exportCsv}>Export</button>
      </div>
      <form className="mt-4 flex flex-wrap items-end gap-3" onSubmit={load}>
        <div>
          <label className="label" htmlFor="from">From</label>
          <input id="from" className="field" type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="to">To</label>
          <input id="to" className="field" type="date" value={to} onChange={(e) => setTo(e.target.value)} />
        </div>
        <button className="btn-secondary" type="submit">Apply</button>
      </form>
      {error && <p className="mt-3 text-sm text-red-800" role="alert">{error}</p>}
      {rows.length === 0 && <p className="mt-3 text-sm text-slate-600">No events in this range.</p>}
      <div className="mt-4 space-y-3 md:hidden">
        {rows.map((row) => (
          <article key={row.date} className="rounded-2xl border border-slate-100 p-3 text-sm">
            <p className="font-semibold">{row.date}</p>
            <p className="mt-1">In {row.checkIn} · Out {row.checkOut}</p>
            <p className="text-slate-600">{row.hours} · {row.source}{row.test ? " · TEST RECORD" : ""}</p>
            <p className="text-slate-600">{row.link}</p>
            <p className="mt-1"><TimenexBadge label={row.timenex} confirmed={row.confirmed} status={row.timenexStatus} /></p>
            {!row.confirmed && row.syncId && (
              <button className="mt-3 min-h-12 font-semibold text-[#1746d1]" type="button" onClick={() => syncDay(row.syncId)}>
                Sync to TimeNex
              </button>
            )}
          </article>
        ))}
      </div>
      <div className="mt-4 hidden overflow-x-auto md:block">
        <table className="w-full min-w-[640px] text-left text-sm">
          <thead className="text-slate-500">
            <tr>
              <th className="py-2 font-medium">Date</th>
              <th className="py-2 font-medium">Check-In</th>
              <th className="py-2 font-medium">Check-Out</th>
              <th className="py-2 font-medium">Working Hours</th>
              <th className="py-2 font-medium">Source</th>
              <th className="py-2 font-medium">Sign-in</th>
              <th className="py-2 font-medium">TimeNex link</th>
              <th className="py-2 font-medium">TimeNex</th>
              <th className="py-2 font-medium">Sync</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.date} className="border-t border-slate-100">
                <td className="py-3">{row.date}</td>
                <td>{row.checkIn}</td>
                <td>{row.checkOut}</td>
                <td>{row.hours}</td>
                <td>{row.source}{row.test ? " · TEST RECORD" : ""}</td>
                <td>{row.signIn}</td>
                <td>{row.link}</td>
                <td><TimenexBadge label={row.timenex} confirmed={row.confirmed} status={row.timenexStatus} /></td>
                <td>
                  {row.confirmed || !row.syncId ? "—" : (
                    <button className="font-semibold text-[#1746d1]" type="button" onClick={() => syncDay(row.syncId)}>
                      Sync to TimeNex
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
    </div>
  );
}

function signInLabel(event: AttendanceEvent | undefined) {
  if (!event) return "—";
  if (event.authentication_label) return event.authentication_label;
  if (event.authentication_method === "PASSWORD_MFA") return "Password and authenticator code";
  if (event.authentication_method === "WEBAUTHN") return "Passkey on this device";
  return "Not recorded";
}

function TimenexBadge({ label, confirmed, status }: { label: string; confirmed?: boolean; status?: string }) {
  const failed = status === "FAILED";
  const tone = confirmed ? "bg-emerald-100 text-emerald-900" : failed ? "bg-red-100 text-red-900" : "bg-slate-100 text-slate-700";
  const text = label === "In only – Out still open" ? label : confirmed ? "Confirmed by TimeNex" : label;
  return <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-semibold ${tone}`}>{text}</span>;
}

function timenexLabel(event: AttendanceEvent | undefined) {
  if (!event) return "—";
  if (event.timenex_label) return event.timenex_label;
  if (event.timenex_confirmed) return "Confirmed by TimeNex";
  if (event.timenex_sync_status === "NOT_MAPPED") return "Saved here · mapping not verified";
  if (event.timenex_sync_status === "AWAITING_CONTRACT") return "Saved here · TimeNex has not confirmed";
  if (event.timenex_sync_status === "MISCONFIGURED") return "Saved here · no verified TimeNex contract";
  return "Saved here · not submitted";
}

function groupDays(events: AttendanceEvent[]) {
  const byDate = new Map<string, AttendanceEvent[]>();
  for (const event of events) {
    const date = event.effective_at.pkt.slice(0, 10);
    const list = byDate.get(date) || [];
    list.push(event);
    byDate.set(date, list);
  }
  return [...byDate.entries()].map(([date, items]) => {
    const checkIn = items.find((item) => item.event_type === "CHECK_IN");
    const checkOut = [...items].reverse().find((item) => item.event_type === "CHECK_OUT");
    let hours = "—";
    if (checkIn && checkOut) {
      const ms = new Date(checkOut.effective_at.pkt).getTime() - new Date(checkIn.effective_at.pkt).getTime();
      if (ms >= 0) {
        const h = Math.floor(ms / 3_600_000);
        const m = Math.floor((ms % 3_600_000) / 60_000);
        hours = `${h}h ${String(m).padStart(2, "0")}m`;
      }
    }
    return {
      date,
      checkIn: checkIn ? checkIn.effective_at.pkt_display.split(", ")[1] || checkIn.effective_at.pkt_display : "—",
      checkOut: checkOut ? checkOut.effective_at.pkt_display.split(", ")[1] || checkOut.effective_at.pkt_display : "—",
      hours,
      source: (checkIn || checkOut)?.source_label || "—",
      signIn: signInLabel(checkIn || checkOut),
      link: (checkIn || checkOut)?.timenex_mapping_label || "Not mapped",
      timenex: timenexLabel(checkOut || checkIn),
      timenexStatus: (checkOut || checkIn)?.timenex_sync_status,
      syncId: (checkOut || checkIn)?.id || "",
      confirmed: Boolean((checkOut || checkIn)?.timenex_confirmed),
      test: items.some((item) => item.is_test_record),
    };
  });
}
