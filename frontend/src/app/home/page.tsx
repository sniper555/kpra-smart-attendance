"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Shell } from "@/components/Shell";
import { api, ApiError, AttendanceEvent, Me, pktDateInput, TimenexReceipt } from "@/lib/api";
import { credentialToJson, requestOptions } from "@/lib/webauthn";

type State = {
  status: string;
  open_session: {
    opened_at: { pkt_display: string; pkt: string };
    started_on_previous_day: boolean;
    check_in_event_id?: string;
    local_date?: string;
  } | null;
  source_label: string;
  can_check_in: boolean;
  can_check_out: boolean;
  check_in_block_reason: string | null;
  check_out_block_reason: string | null;
  is_test_record: boolean;
  authentication_method?: string;
  passkey_required_for_attendance?: boolean;
  passkey_count?: number;
  passkey_verified?: boolean;
  location_checked?: boolean;
  timenex?: TimenexReceipt & {
    mapping_status?: string;
    timenex_employee_id?: string | null;
    authenticates_by_itself?: boolean;
  };
  erp?: { submitted: boolean; confirmed: boolean; message: string };
};
type Summary = {
  month: string;
  check_ins: number;
  check_outs: number;
  days_with_check_in: number;
  timenex_confirmed?: number;
  late_check_ins: number | null;
  early_check_outs: number | null;
  schedule_note: string;
};

export default function HomePage() {
  return (
    <Shell>
      <HomeBody />
    </Shell>
  );
}

function clockParts() {
  const now = new Date();
  const date = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Karachi",
    weekday: "short",
    day: "2-digit",
    month: "short",
    year: "numeric",
  }).format(now);
  const time = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Karachi",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: true,
  }).format(now);
  return { date, time };
}

function durationLabel(startIso: string, endIso: string) {
  const ms = new Date(endIso).getTime() - new Date(startIso).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "—";
  const hours = Math.floor(ms / 3_600_000);
  const minutes = Math.floor((ms % 3_600_000) / 60_000);
  return `${hours}h ${String(minutes).padStart(2, "0")}m`;
}

function HomeBody() {
  const [state, setState] = useState<State | null>(null);
  const [events, setEvents] = useState<AttendanceEvent[]>([]);
  const [recent, setRecent] = useState<AttendanceEvent[]>([]);
  const [weekEvents, setWeekEvents] = useState<AttendanceEvent[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [clock, setClock] = useState(clockParts);
  const [error, setError] = useState("");
  const [syncNote, setSyncNote] = useState("");
  const [forceSync, setForceSync] = useState<{ id: string; message: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [receipt, setReceipt] = useState<{ event: AttendanceEvent; timenex: TimenexReceipt } | null>(null);
  const [name, setName] = useState("");
  const key = useRef<string | null>(null);

  async function load() {
    const [nextState, today, month, history] = await Promise.all([
      api<State>("/api/attendance/me/state"),
      api<{ events: AttendanceEvent[] }>("/api/attendance/me/today"),
      api<Summary>("/api/attendance/me/summary"),
      api<{ events: AttendanceEvent[] }>(`/api/attendance/me/history?date_from=${pktDateInput(6)}&date_to=${pktDateInput(0)}&limit=40`),
    ]);
    setState(nextState);
    setEvents(today.events);
    setSummary(month);
    setWeekEvents(history.events);
    setRecent(history.events.slice(0, 6));
  }

  useEffect(() => {
    api<Me>("/api/auth/me")
      .then((me) => setName(me.user.employee?.full_name || me.user.display_name))
      .catch(() => undefined);
    load().catch((err: unknown) => setError(err instanceof Error ? err.message : "Could not load attendance."));
    const timer = setInterval(() => setClock(clockParts()), 1000);
    return () => clearInterval(timer);
  }, []);

  async function punch() {
    if (!state) return;
    setBusy(true);
    setError("");
    const checkingOut = state.status === "CHECKED_IN";
    if (!key.current) key.current = crypto.randomUUID();
    try {
      if (state.passkey_required_for_attendance && (state.passkey_count ?? 0) === 0) {
        throw new ApiError(409, "PASSKEY_REQUIRED", "Add a passkey on the account page before marking attendance. No fingerprint or face template is stored.");
      }
      if (state.passkey_required_for_attendance && state.authentication_method !== "WEBAUTHN") {
        await verifyAttendancePasskey();
      }
      const data = await api<{ event: AttendanceEvent; timenex: TimenexReceipt }>(checkingOut ? "/api/attendance/check-out" : "/api/attendance/check-in", {
        method: "POST",
        body: JSON.stringify({
          idempotency_key: key.current,
          client_reported_at: new Date().toISOString(),
        }),
      });
      key.current = null;
      setReceipt({ event: data.event, timenex: data.timenex });
      setSyncNote(checkingOut && (data.timenex.message || "").includes("Syncing") ? "Saved locally. Syncing to TimeNex…" : "");
      await load();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) key.current = null;
      setError(err instanceof ApiError ? err.message : "Attendance was not recorded.");
    } finally {
      setBusy(false);
    }
  }

  function syncTargetId() {
    if (state?.open_session?.started_on_previous_day && state.open_session.check_in_event_id) {
      return state.open_session.check_in_event_id;
    }
    const checkout = [...events].reverse().find((event) => event.event_type === "CHECK_OUT");
    if (checkout) return checkout.id;
    if (events[0]) return events[0].id;
    return state?.open_session?.check_in_event_id || null;
  }

  function postSync(id: string, confirm: boolean) {
    setBusy(true);
    setError("");
    const path = `/api/attendance/sync-to-timenex/${id}${confirm ? "?confirm_overwrite=true" : ""}`;
    api<{ message: string; timenex_error?: string | null }>(path, { method: "POST" })
      .then((result) => {
        setForceSync(null);
        setSyncNote(result.message);
        setError(result.timenex_error || "");
        return load();
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.code === "FORCE_REQUIRED") {
          setForceSync({ id, message: err.message });
          return;
        }
        setError(err instanceof ApiError ? err.message : "TimeNex did not accept the day.");
      })
      .finally(() => setBusy(false));
  }

  const checkedIn = state?.status === "CHECKED_IN";
  const blocked = checkedIn ? state?.check_out_block_reason : state?.check_in_block_reason;
  const openAt = state?.open_session?.opened_at.pkt;
  const hours = openAt ? durationLabel(openAt, new Date().toISOString()) : "—";
  const week = weekCounts(weekEvents);

  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <section className="grid gap-4 lg:grid-cols-[1.4fr_0.8fr]">
        <article className={`rounded-3xl p-5 text-white shadow-sm ${checkedIn ? "bg-gradient-to-br from-emerald-500 to-emerald-600" : "bg-gradient-to-br from-slate-600 to-slate-700"}`}>
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-2xl font-semibold">{checkedIn ? "You are Checked In" : "You are not checked in"}</p>
              <p className="mt-1 text-sm text-white/90">
                {name ? `${name} · ` : ""}
                {state?.open_session ? state.open_session.opened_at.pkt_display : clock.date}
              </p>
            </div>
            <span className="rounded-full bg-white/20 px-3 py-1 text-xs font-semibold">{state?.source_label || "Remote attendance"}</span>
          </div>
          <div className="mt-6 flex flex-col gap-3 sm:flex-row sm:flex-wrap">
            <button
              className={`min-h-16 w-full rounded-2xl px-6 text-lg font-semibold text-white disabled:opacity-60 sm:w-auto sm:min-h-12 sm:text-sm ${checkedIn ? "bg-emerald-800" : "bg-[#0b2f86]"}`}
              type="button"
              disabled={busy || !state || Boolean(blocked)}
              onClick={punch}
            >
              {busy ? "Recording…" : checkedIn ? "Check-Out" : "Check-In"}
            </button>
            <Link className="inline-flex min-h-14 w-full items-center justify-center rounded-2xl bg-white px-5 text-base font-semibold text-[#1746d1] sm:w-auto sm:min-h-12 sm:text-sm" href="/history">
              View Details
            </Link>
          </div>
          {blocked && <p className="mt-3 text-sm text-white">{blocked}</p>}
          {state?.is_test_record && <p className="mt-3 text-sm text-amber-100">Test record. This is not an official personnel entry.</p>}
          {syncNote && <p className="mt-3 text-sm text-white">{syncNote}</p>}
          {state?.timenex?.timenex_employee_id && (
            <button
              className="mt-3 min-h-12 rounded-xl bg-white px-5 text-sm font-semibold text-[#1746d1]"
              type="button"
              onClick={() => {
                const target = syncTargetId();
                if (!target) {
                  setError("Check in locally before syncing this day to TimeNex.");
                  return;
                }
                postSync(target, false);
              }}
            >
              Sync to TimeNex
            </button>
          )}
          {forceSync && (
            <div className="mt-3 rounded-xl bg-white p-3 text-slate-800">
              <p className="text-sm font-semibold">Force sync this day</p>
              <p className="mt-1 text-sm">{forceSync.message}</p>
              <div className="mt-3 flex flex-wrap gap-2">
                <button className="min-h-10 rounded-lg bg-[#1746d1] px-3 text-sm font-semibold text-white" type="button" onClick={() => postSync(forceSync.id, true)}>
                  Overwrite TimeNex day with these times
                </button>
                <button className="min-h-10 rounded-lg bg-slate-200 px-3 text-sm font-semibold text-slate-800" type="button" onClick={() => setForceSync(null)}>
                  Cancel
                </button>
              </div>
            </div>
          )}
          {state?.timenex?.message && <p className="mt-3 text-sm text-white/90">{state.timenex.message}</p>}
          {state?.passkey_required_for_attendance && !state.passkey_verified && (state.passkey_count ?? 0) > 0 && (
            <p className="mt-2 text-sm text-white/90">
              {checkedIn ? "Check-Out" : "Check-In"} asks once for the passkey on this device: fingerprint, face, or device PIN. On a phone, confirm with the phone. A passkey does not prove where you are.
            </p>
          )}
          {state?.passkey_required_for_attendance && (state.passkey_count ?? 0) === 0 && (
            <p className="mt-2 text-sm text-white/90">
              A passkey is required. <Link className="font-semibold underline" href="/account">Add a passkey</Link>
            </p>
          )}
        </article>
        <article className="rounded-3xl bg-white p-5 shadow-sm">
          <p className="text-sm text-slate-500">Today&apos;s Date &amp; Time</p>
          <p className="mt-3 text-lg font-medium text-slate-700">{clock.date}</p>
          <p className="text-3xl font-semibold text-[#071a33]">{clock.time}</p>
          <p className="text-sm text-slate-500">(PKT)</p>
          <p className="mt-3 text-xs text-slate-500">The saved attendance time is the server time on the receipt.</p>
        </article>
      </section>

      <section className="rounded-3xl bg-white p-5 shadow-sm">
        <h2 className="text-lg font-semibold">TimeNex link</h2>
        <p className="mt-1 text-sm text-slate-600">Check this before marking attendance. The employee ID does not sign you in, and a saved punch is not a TimeNex confirmation.</p>
        <dl className="mt-3 grid gap-3 text-sm sm:grid-cols-3">
          <div>
            <dt className="text-slate-500">Mapping</dt>
            <dd className="font-medium text-[#071a33]">{mappingLabel(state?.timenex)}</dd>
          </div>
          <div>
            <dt className="text-slate-500">Signed in with</dt>
            <dd className="font-medium text-[#071a33]">{signInLabel(state?.authentication_method)}</dd>
          </div>
          <div>
            <dt className="text-slate-500">Confirmed by TimeNex</dt>
            <dd className="font-medium text-[#071a33]">{state?.timenex?.confirmed ? "Yes" : "No"}</dd>
          </div>
        </dl>
      </section>

      {error && <p className="rounded-xl bg-red-50 px-3 py-2 text-sm text-red-800" role="alert">{error}</p>}

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Monthly Attendance" value={String(summary?.days_with_check_in ?? "—")} note={`Confirmed by TimeNex: ${summary?.timenex_confirmed ?? 0}`} />
        <Stat label="Working Hours" value={checkedIn ? hours : "—"} note={checkedIn ? "Open session, today" : "No open session"} />
        <Stat label="Late Arrivals" value={summary?.late_check_ins === null || summary?.late_check_ins === undefined ? "Off" : String(summary.late_check_ins)} note="This month" />
        <Stat label="Early Departures" value={summary?.early_check_outs === null || summary?.early_check_outs === undefined ? "Off" : String(summary.early_check_outs)} note="This month" />
      </section>
      <p className="text-sm text-slate-600">{summary?.schedule_note}</p>

      <section className="grid gap-4 lg:grid-cols-2">
        <article className="rounded-3xl bg-white p-5 shadow-sm">
          <h2 className="text-lg font-semibold">Attendance this week</h2>
          <div className="mt-4 flex h-40 items-end gap-2">
            {week.map((item) => (
              <div key={item.label} className="flex flex-1 flex-col items-center gap-2">
                <div className="flex h-28 w-full items-end rounded-lg bg-slate-100">
                  <div className="w-full rounded-lg bg-[#1746d1]" style={{ height: `${item.count ? Math.max(16, item.count * 28) : 4}px` }} />
                </div>
                <span className="text-xs text-slate-500">{item.label}</span>
              </div>
            ))}
          </div>
          <p className="mt-2 text-xs text-slate-500">Check-ins saved in this application over the last seven days. Confirmed by TimeNex: {weekEvents.filter((event) => event.event_type === "CHECK_IN" && event.timenex_confirmed).length}.</p>
        </article>
        <article className="rounded-3xl bg-white p-5 shadow-sm">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold">Recent Activity</h2>
            <Link className="text-sm font-semibold text-[#1746d1]" href="/history">View all</Link>
          </div>
          <p className="mt-1 text-xs text-slate-500">The TimeNex link is the current mapping. The second line says whether that punch was confirmed.</p>
          <ul className="mt-3 divide-y">
            {(recent.length ? recent : events).map((event) => (
              <li key={event.id} className="flex items-start justify-between gap-3 py-3 text-sm">
                <div>
                  <p className="font-medium">{event.event_type === "CHECK_IN" ? "Check-In" : "Check-Out"} · {event.effective_at.pkt_display}</p>
                  <p className="text-slate-500">{event.source_label}{event.is_test_record ? " · TEST RECORD" : ""}</p>
                  <p className="text-slate-600">{event.timenex_mapping_label || "Not mapped"} · {event.timenex_label || "Saved here · not submitted"}</p>
                </div>
              </li>
            ))}
            {recent.length === 0 && events.length === 0 && <li className="py-3 text-sm text-slate-500">No attendance recorded yet.</li>}
          </ul>
        </article>
      </section>

      {receipt && (
        <dialog open className="w-[min(32rem,calc(100%-2rem))] rounded-2xl p-0">
          <div className="p-5">
            <h2 className="text-xl font-semibold">Saved in this application</h2>
            <dl className="mt-3 space-y-2 text-sm">
              <div><dt className="text-slate-500">Event ID</dt><dd className="font-mono">{receipt.event.id}</dd></div>
              <div><dt className="text-slate-500">Server time</dt><dd>{receipt.event.server_received_at.pkt_display}</dd></div>
              <div><dt className="text-slate-500">Source</dt><dd>{receipt.event.source_label}</dd></div>
              <div><dt className="text-slate-500">Sign-in</dt><dd>{signInLabel(receipt.event.authentication_method)}</dd></div>
              <div><dt className="text-slate-500">Device</dt><dd>{deviceLabel(receipt.event.client_platform)}</dd></div>
              <div><dt className="text-slate-500">Location</dt><dd>Not checked. A passkey does not prove where you are.</dd></div>
              <div><dt className="text-slate-500">TimeNex link</dt><dd>{receipt.event.timenex_mapping_label || "Not mapped"}</dd></div>
              <div><dt className="text-slate-500">TimeNex</dt><dd>{receipt.event.timenex_confirmed ? "Confirmed by TimeNex" : receipt.event.timenex_label || receipt.timenex.message}</dd></div>
            </dl>
            <button className="btn-primary mt-4 w-full" type="button" onClick={() => setReceipt(null)}>Close</button>
          </div>
        </dialog>
      )}
    </div>
  );
}

function mappingLabel(timenex: State["timenex"]) {
  if (!timenex || timenex.mapping_status === "UNMAPPED" || !timenex.mapping_status) return "Not mapped";
  const id = timenex.timenex_employee_id ? ` · ${timenex.timenex_employee_id}` : "";
  if (timenex.mapping_status === "VERIFIED") return `Verified${id}`;
  if (timenex.mapping_status === "PENDING") return `Pending${id}`;
  if (timenex.mapping_status === "REJECTED") return `Rejected${id}`;
  return timenex.mapping_status;
}

async function verifyAttendancePasskey() {
  if (!window.PublicKeyCredential) {
    throw new ApiError(409, "PASSKEY_REQUIRED", "This browser does not support passkeys.");
  }
  const options = await api<{ challenge_id: string; public_key: Record<string, unknown> }>("/api/attendance/passkey/options", {
    method: "POST",
  });
  const credential = (await navigator.credentials.get({
    publicKey: requestOptions(options.public_key),
  })) as PublicKeyCredential | null;
  if (!credential) {
    throw new ApiError(401, "AUTH_FAILED", "No passkey was selected.");
  }
  await api("/api/attendance/passkey/verify", {
    method: "POST",
    body: JSON.stringify({ challenge_id: options.challenge_id, credential: credentialToJson(credential) }),
  });
}

function deviceLabel(platform: string | null | undefined) {
  if (platform === "android") return "Android browser";
  if (platform === "ios") return "iPhone or iPad browser";
  if (platform === "windows") return "Windows browser";
  if (platform === "mac") return "Mac browser";
  return "Browser";
}

function signInLabel(method: string | undefined) {
  if (method === "WEBAUTHN") return "Passkey with user verification";
  if (method === "PASSWORD_MFA") return "Password and authenticator code";
  return "—";
}

function Stat({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <article className="rounded-3xl bg-white p-4 shadow-sm">
      <p className="text-sm text-slate-500">{label}</p>
      <p className="mt-2 text-2xl font-semibold text-[#071a33]">{value}</p>
      <p className="text-xs text-slate-500">{note}</p>
    </article>
  );
}

function weekCounts(events: AttendanceEvent[]) {
  const labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const counts = labels.map((label) => ({ label, count: 0 }));
  for (const event of events) {
    if (event.event_type !== "CHECK_IN") continue;
    const weekday = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Karachi", weekday: "short" }).format(new Date(event.effective_at.pkt));
    const index = counts.findIndex((item) => item.label === weekday);
    if (index >= 0) counts[index].count += 1;
  }
  return counts;
}
