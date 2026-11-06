"use client";

import { FormEvent, useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { api, ApiError } from "@/lib/api";

type RequestRow = {
  id: string;
  kind: string;
  kind_label: string;
  reason: string;
  status: string;
  status_label: string;
  review_reason: string | null;
  timenex_mapping_label: string;
  timenex_confirmed: boolean;
  timenex_note: string;
  requested_start_at: { pkt_display: string } | null;
  requested_end_at: { pkt_display: string } | null;
  created_at: { pkt_display: string };
};

export default function CorrectionsPage() {
  return <Shell><CorrectionsBody /></Shell>;
}

function CorrectionsBody() {
  const [rows, setRows] = useState<RequestRow[]>([]);
  const [kind, setKind] = useState("MISSED_CHECK_OUT");
  const [reason, setReason] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  async function load() {
    const data = await api<{ requests: RequestRow[] }>("/api/corrections/mine");
    setRows(data.requests);
  }

  useEffect(() => {
    load().catch((err: unknown) => setError(err instanceof Error ? err.message : "Could not load requests."));
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setMessage("");
    try {
      await api("/api/corrections", {
        method: "POST",
        body: JSON.stringify({
          kind,
          reason,
          requested_start_at: start ? new Date(start).toISOString() : null,
          requested_end_at: end ? new Date(end).toISOString() : null,
        }),
      });
      setReason("");
      setMessage("Request submitted. An administrator must approve it before any attendance changes.");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The request was not saved.");
    }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <form className="card space-y-3 p-5" onSubmit={submit}>
        <h1 className="text-2xl font-semibold">Request a correction</h1>
        <p className="text-sm text-slate-600">An approved request adds an administrator correction in this application. It does not delete history, and it is not sent to TimeNex.</p>
        <div>
          <label className="label" htmlFor="kind">Type</label>
          <select id="kind" className="field" value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="MISSED_CHECK_OUT">Missed check-out</option>
            <option value="MISSED_CHECK_IN">Missed check-in</option>
            <option value="HISTORICAL_PAIR">Historical check-in and check-out</option>
          </select>
        </div>
        {(kind === "MISSED_CHECK_IN" || kind === "HISTORICAL_PAIR") && (
          <div>
            <label className="label" htmlFor="start">Check-in time</label>
            <input id="start" className="field" type="datetime-local" required value={start} onChange={(e) => setStart(e.target.value)} />
          </div>
        )}
        {(kind === "MISSED_CHECK_OUT" || kind === "HISTORICAL_PAIR") && (
          <div>
            <label className="label" htmlFor="end">Check-out time</label>
            <input id="end" className="field" type="datetime-local" required value={end} onChange={(e) => setEnd(e.target.value)} />
          </div>
        )}
        <div>
          <label className="label" htmlFor="reason">Reason</label>
          <textarea id="reason" className="field min-h-28" required minLength={10} value={reason} onChange={(e) => setReason(e.target.value)} />
        </div>
        {error && <p className="text-sm text-red-800" role="alert">{error}</p>}
        {message && <p className="text-sm text-green-800">{message}</p>}
        <button className="btn-primary" type="submit">Submit request</button>
      </form>
      <section className="card p-5">
        <h2 className="text-lg font-semibold">Your requests</h2>
        <ul className="mt-3 space-y-3">
          {rows.map((row) => (
            <li key={row.id} className="rounded-xl bg-brand-50 p-3 text-sm">
              <p className="font-medium">{row.kind_label || row.kind} · {row.status_label || row.status}</p>
              <p>{row.reason}</p>
              <p className="mt-1"><span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-semibold ${row.timenex_confirmed ? "bg-emerald-100 text-emerald-900" : "bg-slate-100 text-slate-700"}`}>{row.timenex_confirmed ? "Confirmed by TimeNex" : "Not confirmed by TimeNex"}</span></p>
              {row.review_reason && <p className="text-slate-600">Review: {row.review_reason}</p>}
            </li>
          ))}
          {rows.length === 0 && <li className="text-sm text-slate-600">No requests yet.</li>}
        </ul>
      </section>
    </div>
  );
}
