"use client";

import { FormEvent, useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { Account, api, apiUrl, ApiError, AttendanceEvent, pktDateInput, setCsrf } from "@/lib/api";

type Tab = "accounts" | "mark" | "attendance" | "corrections" | "audit" | "devices" | "policy" | "timenex" | "qa";

export default function AdminPage() {
  return (
    <Shell>
      <AdminBody />
    </Shell>
  );
}

function AdminBody() {
  const [tab, setTab] = useState<Tab>("accounts");
  const [environment, setEnvironment] = useState("");
  useEffect(() => {
    const requested = new URLSearchParams(window.location.search).get("tab");
    if (requested === "accounts" || requested === "mark" || requested === "attendance" || requested === "corrections" || requested === "audit" || requested === "devices" || requested === "policy" || requested === "timenex" || requested === "qa") {
      setTab(requested);
    }
    api<{ environment: string }>("/api/auth/me")
      .then((data) => setEnvironment(data.environment))
      .catch(() => undefined);
  }, []);
  const tabs: [Tab, string][] = [
    ["accounts", "Accounts"],
    ["mark", "Mark attendance"],
    ["attendance", "Attendance"],
    ["corrections", "Corrections"],
    ["audit", "Audit"],
    ["devices", "Devices"],
    ["policy", "Office timings"],
    ["timenex", "TimeNex mapping"],
  ];
  if (environment === "development") tabs.push(["qa", "QA harness"]);
  return (
    <div>
      <h1 className="text-2xl font-semibold">Administration</h1>
      <div className="mt-3 flex gap-2 overflow-x-auto">
        {tabs.map(([id, label]) => (
          <button key={id} className={tab === id ? "btn-primary" : "btn-secondary"} type="button" onClick={() => setTab(id)}>
            {label}
          </button>
        ))}
      </div>
      <div className="mt-4">
        {tab === "accounts" && <Accounts />}
        {tab === "mark" && <MarkAttendance />}
        {tab === "attendance" && <Attendance />}
        {tab === "corrections" && <Corrections />}
        {tab === "audit" && <Audit />}
        {tab === "devices" && <Devices />}
        {tab === "policy" && <Policy />}
        {tab === "timenex" && <TimenexMapping />}
        {tab === "qa" && <QaHarness />}
      </div>
    </div>
  );
}

function mappingText(row: Account) {
  const item = row.timenex_mapping;
  if (!item) return "Not mapped";
  if (item.verification_status === "VERIFIED") return `Verified · ${item.timenex_employee_id}`;
  if (item.verification_status === "PENDING") return `Pending · ${item.timenex_employee_id}`;
  if (item.verification_status === "REJECTED") return `Rejected · ${item.timenex_employee_id}`;
  return item.verification_status;
}

function Accounts() {
  const [rows, setRows] = useState<Account[]>([]);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [mapping, setMapping] = useState("all");
  const [editing, setEditing] = useState<Account | null>(null);
  const [editForm, setEditForm] = useState({
    display_name: "",
    employee_number: "",
    designation: "",
    department_name: "",
    device_pin: "",
  });
  const [form, setForm] = useState({
    email: "",
    temporary_password: "",
    display_name: "",
    employee_number: "",
    designation: "",
    department_name: "",
    device_pin: "",
    remote_attendance_enabled: false,
    is_test_record: true,
  });

  async function load() {
    const data = await api<{ managers: Account[] }>("/api/admin/managers");
    setRows(data.managers);
  }
  useEffect(() => {
    load().catch((err: unknown) => setError(err instanceof Error ? err.message : "Could not load accounts."));
  }, []);

  async function create(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      await api("/api/admin/managers", { method: "POST", body: JSON.stringify(form) });
      setForm({ ...form, email: "", temporary_password: "", display_name: "", employee_number: "" });
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create the account.");
    }
  }

  async function toggleRemote(row: Account) {
    if (!row.employee) return;
    await api(`/api/admin/managers/${row.id}`, {
      method: "PATCH",
      body: JSON.stringify({ remote_attendance_enabled: !row.employee.remote_attendance_enabled }),
    });
    await load();
  }

  async function setActive(row: Account, active: boolean) {
    await api(`/api/admin/managers/${row.id}/${active ? "reactivate" : "deactivate"}`, { method: "POST" });
    await load();
  }

  async function resetMfa(row: Account) {
    await api(`/api/admin/managers/${row.id}/reset-mfa`, { method: "POST" });
    await load();
  }

  async function revokePasskeys(row: Account) {
    const reason = window.prompt("Reason for revoking passkeys");
    if (!reason) return;
    setError("");
    setMessage("");
    try {
      const result = await api<{ revoked: number; message: string }>(`/api/admin/managers/${row.id}/passkeys/revoke`, {
        method: "POST",
        body: JSON.stringify({ reason }),
      });
      setMessage(result.message);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The passkeys were not revoked.");
    }
  }

  function startEdit(row: Account) {
    setError("");
    setMessage("");
    setEditing(row);
    setEditForm({
      display_name: row.display_name,
      employee_number: row.employee?.employee_number || "",
      designation: row.employee?.designation || "",
      department_name: row.employee?.department || "",
      device_pin: row.employee?.device_pin || "",
    });
  }

  async function saveEdit(event: FormEvent) {
    event.preventDefault();
    if (!editing) return;
    setError("");
    try {
      await api(`/api/admin/managers/${editing.id}`, { method: "PATCH", body: JSON.stringify(editForm) });
      setEditing(null);
      setMessage("Account updated.");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not update the account.");
    }
  }

  const needle = query.trim().toLowerCase();
  const visible = rows.filter((row) => {
    if (status === "active" && !row.is_active) return false;
    if (status === "inactive" && row.is_active) return false;
    const verification = row.timenex_mapping?.verification_status || "UNMAPPED";
    if (mapping !== "all" && verification !== mapping) return false;
    if (!needle) return true;
    const haystack = [
      row.display_name,
      row.email,
      row.employee?.employee_number,
      row.employee?.device_pin,
      row.employee?.department,
      row.employee?.designation,
      row.timenex_mapping?.timenex_employee_id,
      verification,
    ].join(" ").toLowerCase();
    return haystack.includes(needle);
  });

  return (
    <div className="space-y-4">
      <form className="card grid gap-3 p-4 md:grid-cols-2" onSubmit={create}>
        <h2 className="md:col-span-2 text-lg font-semibold">New manager</h2>
        {[
          ["email", "Email", "email"],
          ["temporary_password", "Temporary password", "text"],
          ["display_name", "Name", "text"],
          ["employee_number", "Employee number", "text"],
          ["designation", "Designation", "text"],
          ["department_name", "Department", "text"],
          ["device_pin", "Device PIN", "text"],
        ].map(([key, label, type]) => (
          <div key={key}>
            <label className="label" htmlFor={key}>{label}</label>
            <input id={key} className="field" type={type} value={(form as Record<string, string | boolean>)[key] as string} onChange={(e) => setForm({ ...form, [key]: e.target.value })} required={["email", "temporary_password", "display_name", "employee_number"].includes(key)} />
          </div>
        ))}
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.remote_attendance_enabled} onChange={(e) => setForm({ ...form, remote_attendance_enabled: e.target.checked })} /> Remote attendance enabled</label>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.is_test_record} onChange={(e) => setForm({ ...form, is_test_record: e.target.checked })} /> Test record</label>
        {error && <p className="md:col-span-2 text-sm text-red-800" role="alert">{error}</p>}
        <button className="btn-primary md:col-span-2" type="submit">Add employee</button>
      </form>
      {editing && (
        <form className="card grid gap-3 p-4 md:grid-cols-2" onSubmit={saveEdit}>
          <h2 className="md:col-span-2 text-lg font-semibold">Edit {editing.email}</h2>
          <div><label className="label" htmlFor="edit-name">Name</label><input id="edit-name" className="field" value={editForm.display_name} onChange={(e) => setEditForm({ ...editForm, display_name: e.target.value })} required /></div>
          <div><label className="label" htmlFor="edit-number">Employee number</label><input id="edit-number" className="field" value={editForm.employee_number} onChange={(e) => setEditForm({ ...editForm, employee_number: e.target.value })} required /></div>
          <div><label className="label" htmlFor="edit-designation">Designation</label><input id="edit-designation" className="field" value={editForm.designation} onChange={(e) => setEditForm({ ...editForm, designation: e.target.value })} /></div>
          <div><label className="label" htmlFor="edit-department">Department</label><input id="edit-department" className="field" value={editForm.department_name} onChange={(e) => setEditForm({ ...editForm, department_name: e.target.value })} /></div>
          <div><label className="label" htmlFor="edit-pin">Device PIN</label><input id="edit-pin" className="field" value={editForm.device_pin} onChange={(e) => setEditForm({ ...editForm, device_pin: e.target.value })} /></div>
          <div className="flex items-end gap-2">
            <button className="btn-primary" type="submit">Save</button>
            <button className="btn-secondary" type="button" onClick={() => setEditing(null)}>Cancel</button>
          </div>
        </form>
      )}
      <section className="card p-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold">Employee Management</h2>
            <p className="text-sm text-slate-600">A TimeNex employee ID is a link, not a password. “Can check in here” means a browser punch can be saved in this application. It does not mean TimeNex confirmed it.</p>
          </div>
          <p className="text-sm text-slate-500">{visible.length} of {rows.length}</p>
        </div>
        <div className="mt-3 flex flex-wrap gap-3">
          <input className="field max-w-sm" placeholder="Search by name, ID, PIN or TimeNex ID" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Search employees" />
          <select className="field max-w-[10rem]" aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="all">All status</option>
            <option value="active">Active</option>
            <option value="inactive">Inactive</option>
          </select>
          <select className="field max-w-[12rem]" aria-label="TimeNex mapping" value={mapping} onChange={(e) => setMapping(e.target.value)}>
            <option value="all">All mappings</option>
            <option value="VERIFIED">Verified</option>
            <option value="PENDING">Pending</option>
            <option value="REJECTED">Rejected</option>
            <option value="UNMAPPED">Not mapped</option>
          </select>
        </div>
        {message && <p className="mt-3 text-sm text-green-800">{message}</p>}
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[860px] text-left text-sm">
            <thead className="bg-brand-50">
              <tr>
                <th className="p-3">Name</th>
                <th>Employee ID</th>
                <th>PIN</th>
                <th>Department</th>
                <th>Status</th>
                <th>Browser check-in</th>
                <th>TimeNex</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((row) => (
                <tr key={row.id} className="border-t border-slate-100">
                  <td className="p-3">
                    <span className="font-medium">{row.display_name}</span>
                    {row.employee?.is_test_record ? " · TEST" : ""}
                    <span className="block text-xs text-slate-500">{row.email}</span>
                  </td>
                  <td>{row.employee?.employee_number || "—"}</td>
                  <td>{row.employee?.device_pin || "—"}</td>
                  <td>{row.employee?.department || "—"}</td>
                  <td>{row.is_active ? "Active" : "Inactive"}</td>
                  <td>{row.remote_check_in || "—"}</td>
                  <td>{mappingText(row)}</td>
                  <td className="space-x-2 p-3">
                    <button className="btn-secondary" type="button" onClick={() => startEdit(row)}>Edit</button>
                    {row.employee && <button className="btn-secondary" type="button" onClick={() => toggleRemote(row)}>{row.employee.remote_attendance_enabled ? "Remote on" : "Remote off"}</button>}
                    <button className="btn-secondary" type="button" onClick={() => setActive(row, !row.is_active)}>{row.is_active ? "Deactivate" : "Reactivate"}</button>
                    <button className="btn-secondary" type="button" onClick={() => resetMfa(row)}>Reset MFA</button>
                    {(row.passkey_count || 0) > 0 && <button className="btn-secondary" type="button" onClick={() => revokePasskeys(row)}>Revoke passkeys</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {visible.length === 0 && <p className="py-3 text-sm text-slate-600">No employees match this search.</p>}
        </div>
      </section>
    </div>
  );
}

function MarkAttendance() {
  const [rows, setRows] = useState<Account[]>([]);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState("");
  const [day, setDay] = useState(pktDateInput(1));
  const [checkIn, setCheckIn] = useState("09:00");
  const [checkOut, setCheckOut] = useState("");
  const [reason, setReason] = useState("");
  const [pushDay, setPushDay] = useState(false);
  const [message, setMessage] = useState("");
  const [pushState, setPushState] = useState<"skipped" | "confirmed" | "failed" | "">("");
  const [timenexError, setTimenexError] = useState("");
  const [saved, setSaved] = useState<AttendanceEvent[]>([]);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api<{ managers: Account[] }>("/api/admin/managers")
      .then((data) => setRows(data.managers.filter((row) => row.employee && row.is_active)))
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Could not load employees."));
  }, []);

  const selectedPerson = rows.find((row) => row.employee?.employee_number === selected);
  const verified = selectedPerson?.timenex_mapping?.verification_status === "VERIFIED";
  const today = pktDateInput(0);
  const outMissingWarning = Boolean(day && day < today && !checkOut.trim());
  const needle = query.trim().toLowerCase();
  const matches = rows.filter((row) => {
    if (!needle) return true;
    return [row.display_name, row.employee?.employee_number, row.employee?.full_name, row.timenex_mapping?.timenex_employee_id].join(" ").toLowerCase().includes(needle);
  });

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (saving) return;
    setError("");
    setMessage("");
    setPushState("");
    setTimenexError("");
    setSaved([]);
    const employee = rows.find((row) => row.employee?.employee_number === selected)?.employee;
    if (!employee) {
      setError("Choose an employee from this application.");
      return;
    }
    if (!validClock(checkIn)) {
      setError("Enter the in time as HH:MM, for example 09:00.");
      return;
    }
    if (checkOut && !validClock(checkOut)) {
      setError("Enter the out time as HH:MM, or leave it blank.");
      return;
    }
    const body: { employee_number: string; reason: string; check_in_at?: string; check_out_at?: string; push_to_timenex?: boolean } = {
      employee_number: employee.employee_number,
      reason,
      push_to_timenex: verified && pushDay,
    };
    if (checkIn) body.check_in_at = `${day}T${checkIn.slice(0, 5)}:00+05:00`;
    if (checkOut) body.check_out_at = `${day}T${checkOut.slice(0, 5)}:00+05:00`;
    setSaving(true);
    try {
      const result = await api<{
        message: string;
        source_label: string;
        timenex_confirmed: boolean;
        timenex_error?: string | null;
        timenex_push?: "skipped" | "confirmed" | "failed";
        events: AttendanceEvent[];
      }>(
        "/api/admin/attendance/mark",
        { method: "POST", body: JSON.stringify(body) },
      );
      setSaved(result.events);
      const nextPush = result.timenex_push || (result.timenex_confirmed ? "confirmed" : "skipped");
      setPushState(nextPush);
      setTimenexError(result.timenex_error || "");
      if (nextPush === "confirmed") setMessage(result.message || "Day replaced in TimeNex");
      else if (nextPush === "failed") setMessage(result.timenex_error || result.message);
      else setMessage(`${result.message} Source: ${result.source_label}.`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The attendance service did not finish answering. Check History before marking this day again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="card space-y-3 p-4" onSubmit={submit}>
      <h2 className="text-lg font-semibold">Mark attendance</h2>
      <p className="text-sm text-slate-600">Choose any person stored in this application. The mark is an administrator correction and it is not a fingerprint. It is saved here. When that person has a verified TimeNex link, the checkbox below can also send the date, in time, and out time you entered. The checkbox stays off until you tick it.</p>
      <input className="field" aria-label="Find employee" placeholder="Search by name or employee ID" value={query} onChange={(e) => setQuery(e.target.value)} />
      <select className="field" aria-label="Employee" value={selected} onChange={(e) => { setSelected(e.target.value); setPushDay(false); }} required>
        <option value="">Select an employee</option>
        {matches.map((row) => (
          <option key={row.id} value={row.employee?.employee_number}>{row.display_name} · {row.employee?.employee_number} · {mappingText(row)}</option>
        ))}
      </select>
      {selectedPerson && (
        <p className="rounded-xl bg-slate-50 px-3 py-2 text-sm text-slate-700">
          TimeNex link: {mappingText(selectedPerson)}. This mark is saved here as an administrator correction.{verified ? " Tick the checkbox to replace this day in TimeNex." : " It is not sent to TimeNex."}
        </p>
      )}
      <div className="grid gap-3 md:grid-cols-3">
        <div><label className="label" htmlFor="mark-day">Date</label><input id="mark-day" className="field" type="date" value={day} onChange={(e) => setDay(e.target.value)} required /></div>
        <ClockField id="mark-in" label="In time" value={checkIn} onChange={setCheckIn} />
        <ClockField id="mark-out" label="Out time" value={checkOut} onChange={setCheckOut} optional />
      </div>
      {outMissingWarning && <p className="text-sm text-amber-900">Out time is missing – the day will be marked with only time in.</p>}
      <textarea className="field min-h-20" aria-label="Reason" placeholder="Reason for this mark" value={reason} onChange={(e) => setReason(e.target.value)} required />
      {verified && (
        <label className="flex items-start gap-2 text-sm text-slate-800">
          <input
            className="mt-1"
            type="checkbox"
            checked={pushDay}
            onChange={(e) => setPushDay(e.target.checked)}
          />
          <span>
            Also push this day to TimeNex
            <span className="mt-1 block text-amber-900">This will overwrite the entire day in TimeNex for this employee, including any existing device punch or previous mark. This cannot be undone.</span>
          </span>
        </label>
      )}
      {error && <p className="rounded-xl bg-red-50 px-3 py-2 text-sm text-red-800" role="alert">{error}</p>}
      {message && pushState === "confirmed" && <p className="rounded-xl bg-emerald-50 px-3 py-2 text-sm font-semibold text-emerald-900" role="status">{message}</p>}
      {message && pushState === "failed" && <p className="rounded-xl bg-red-50 px-3 py-2 text-sm text-red-800" role="alert">{timenexError || message}</p>}
      {message && pushState !== "confirmed" && pushState !== "failed" && <p className="text-sm text-green-800">{message}</p>}
      {saved.length > 0 && <p className="text-sm text-slate-800">{markStatus(saved)}</p>}
      <button className="btn-primary" type="submit" disabled={saving}>{saving ? "Saving this day…" : "Mark attendance"}</button>
    </form>
  );
}

function validClock(value: string) {
  return /^([01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?$/.test(value);
}

function ClockField({ id, label, value, onChange, optional = false }: { id: string; label: string; value: string; onChange: (value: string) => void; optional?: boolean }) {
  return (
    <div>
      <label className="label" htmlFor={id}>{label}{optional ? " (optional)" : ""}</label>
      <input
        id={id}
        className="field"
        type="time"
        step={60}
        autoComplete="off"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}

function markStatus(events: AttendanceEvent[]) {
  const confirmed = events.some((event) => event.timenex_confirmed);
  const id = events.find((event) => event.timenex_employee_id)?.timenex_employee_id;
  const hasIn = events.some((event) => event.event_type === "CHECK_IN");
  const hasOut = events.some((event) => event.event_type === "CHECK_OUT");
  if (confirmed && id && hasIn && hasOut) return `Check-in + Check-out · Verified · ${id} · Confirmed by TimeNex`;
  if (confirmed && id && hasIn && !hasOut) return `Check-in · Verified · ${id} · Confirmed by TimeNex (Out still open)`;
  return events.map((event) => `${event.event_type === "CHECK_IN" ? "Check-in" : "Check-out"} · ${event.timenex_mapping_label || "Not mapped"} · ${event.timenex_label || "Saved here · not submitted"}`).join(" ");
}

function authLabel(method: string | null | undefined) {
  if (method === "PASSWORD_MFA") return "Password and authenticator code";
  if (method === "WEBAUTHN") return "Passkey on this device";
  return "—";
}

function TimenexBadge({ label, confirmed, status }: { label: string; confirmed?: boolean; status?: string }) {
  const failed = status === "FAILED";
  const tone = confirmed ? "bg-emerald-100 text-emerald-900" : failed ? "bg-red-100 text-red-900" : "bg-slate-100 text-slate-700";
  const text = label === "In only – Out still open" ? label : confirmed ? "Confirmed by TimeNex" : label;
  return <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-semibold ${tone}`}>{text}</span>;
}

function timenexLine(event: AttendanceEvent) {
  if (event.timenex_confirmed) return "Confirmed by TimeNex";
  if (event.timenex_sync_status === "NOT_MAPPED") return "Saved here · mapping not verified";
  if (event.timenex_sync_status === "AWAITING_CONTRACT") return "Saved here · TimeNex has not confirmed";
  if (event.timenex_sync_status === "MISCONFIGURED") return "Saved here · no verified TimeNex contract";
  return "Saved here · not submitted";
}

function Attendance() {
  const [from, setFrom] = useState(pktDateInput(7));
  const [to, setTo] = useState(pktDateInput(0));
  const [source, setSource] = useState("");
  const [events, setEvents] = useState<AttendanceEvent[]>([]);
  const [error, setError] = useState("");

  async function load(event?: FormEvent) {
    event?.preventDefault();
    const params = new URLSearchParams({ date_from: from, date_to: to, include_flagged: "true" });
    if (source) params.set("source", source);
    try {
      const data = await api<{ events: AttendanceEvent[] }>(`/api/admin/attendance?${params.toString()}`);
      setEvents(data.events);
      setError("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load attendance.");
    }
  }
  useEffect(() => { load().catch(() => undefined); }, []);

  async function exportCsv() {
    const me = await api<{ csrf_token: string }>("/api/auth/me");
    setCsrf(me.csrf_token);
    const response = await fetch(apiUrl("/api/admin/attendance/export"), {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": me.csrf_token },
      body: JSON.stringify({ date_from: from, date_to: to, source: source || null, include_flagged: true }),
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
    link.download = "attendance-export.csv";
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="space-y-4">
      <DailyBoard />
      <MonthlyBoard />
    <section className="card p-4">
      <form className="flex flex-wrap items-end gap-3" onSubmit={load}>
        <div><label className="label" htmlFor="afrom">From</label><input id="afrom" className="field" type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></div>
        <div><label className="label" htmlFor="ato">To</label><input id="ato" className="field" type="date" value={to} onChange={(e) => setTo(e.target.value)} /></div>
        <div>
          <label className="label" htmlFor="source">Source</label>
          <select id="source" className="field" value={source} onChange={(e) => setSource(e.target.value)}>
            <option value="">All</option>
            <option value="REMOTE_WEB">Remote web</option>
            <option value="REMOTE_MOBILE">Remote mobile</option>
            <option value="PHYSICAL_BIOMETRIC">Physical terminal</option>
            <option value="ADMIN_CORRECTION">Administrator correction</option>
            <option value="TEST_DATA">QA test data</option>
          </select>
        </div>
        <button className="btn-secondary" type="submit">Filter</button>
        <button className="btn-primary" type="button" onClick={exportCsv}>Export CSV</button>
      </form>
      <p className="mt-3 text-sm text-slate-600">The file is a record from this application. It includes the TimeNex link and a separate column for whether TimeNex confirmed the row.</p>
      {error && <p className="mt-3 text-sm text-red-800">{error}</p>}
      <ul className="mt-4 divide-y">
        {events.map((event) => (
          <li key={event.id} className="py-2 text-sm">
            <span className="font-medium">{event.employee_name}</span> {event.employee_number} · {event.event_type === "CHECK_IN" ? "Check-in" : "Check-out"} · {event.effective_at.pkt_display}
            <span className="block text-brand-700">{event.source_label}{event.is_test_record ? " · TEST RECORD" : ""}{event.status === "FLAGGED" ? " · flagged" : ""}</span>
            <span className="mt-1 block"><TimenexBadge label={event.timenex_label || timenexLine(event)} confirmed={Boolean(event.timenex_confirmed)} status={event.timenex_sync_status} /></span>
          </li>
        ))}
        {events.length === 0 && <li className="py-2 text-sm text-slate-600">No rows in this filter.</li>}
      </ul>
    </section>
    </div>
  );
}

function DailyBoard() {
  const [data, setData] = useState<{
    date_pkt: string;
    counts: { checked_in: number; checked_out: number; no_record: number };
    rows: Array<{
      employee_number: string;
      employee_name: string;
      department: string | null;
      is_test_record: boolean;
      status: string;
      source_label: string | null;
      last_event_at: { pkt_display: string } | null;
      open_since_previous_day: boolean;
      authentication_label: string | null;
      timenex_label: string | null;
      timenex_confirmed: boolean;
      timenex_mapping_label: string;
    }>;
    timenex_confirmed: number;
  } | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api<NonNullable<typeof data>>("/api/admin/attendance/today")
      .then(setData)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Could not load today's status."));
  }, []);

  const label: Record<string, string> = {
    CHECKED_IN: "Checked in",
    CHECKED_OUT: "Checked out",
    NO_RECORD: "No record today",
    RECORDED: "Recorded",
  };

  return (
    <section className="card p-4">
      <h2 className="text-lg font-semibold">Today · {data?.date_pkt || "…"}</h2>
      <p className="mt-1 text-sm text-slate-600">Status comes from records stored in this application. A missing row is not marked absent, and remote attendance is not shown as a terminal punch. Confirmed by TimeNex: {data?.timenex_confirmed ?? 0}.</p>
      {error && <p className="mt-3 text-sm text-red-800">{error}</p>}
      <div className="mt-3 grid gap-3 sm:grid-cols-3">
        <article className="rounded-2xl bg-emerald-50 p-3"><p className="text-sm text-slate-600">Checked in</p><p className="text-2xl font-semibold">{data?.counts.checked_in ?? "—"}</p></article>
        <article className="rounded-2xl bg-slate-100 p-3"><p className="text-sm text-slate-600">Checked out</p><p className="text-2xl font-semibold">{data?.counts.checked_out ?? "—"}</p></article>
        <article className="rounded-2xl bg-amber-50 p-3"><p className="text-sm text-slate-600">No record today</p><p className="text-2xl font-semibold">{data?.counts.no_record ?? "—"}</p></article>
      </div>
      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[720px] text-left text-sm">
          <thead className="text-slate-500"><tr><th className="py-2">Name</th><th>Employee ID</th><th>Department</th><th>Status</th><th>Source</th><th>Sign-in</th><th>TimeNex link</th><th>TimeNex</th></tr></thead>
          <tbody>
            {(data?.rows || []).map((row) => (
              <tr key={row.employee_number} className="border-t border-slate-100">
                <td className="py-2 font-medium">{row.employee_name}{row.is_test_record ? " · TEST" : ""}</td>
                <td>{row.employee_number}</td>
                <td>{row.department || "—"}</td>
                <td>{label[row.status] || row.status}{row.open_since_previous_day ? " · previous day" : ""}</td>
                <td>{row.source_label || "—"}{row.last_event_at ? ` · ${row.last_event_at.pkt_display}` : ""}</td>
                <td>{row.authentication_label || "—"}</td>
                <td>{row.timenex_mapping_label || "Not mapped"}</td>
                <td>{row.timenex_label || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function MonthlyBoard() {
  const [month, setMonth] = useState(pktDateInput(0).slice(0, 7));
  const [data, setData] = useState<{
    month: string;
    employees_with_check_in: number;
    employees_without_check_in: number;
    rows: Array<{
      employee_number: string;
      employee_name: string;
      department: string | null;
      is_test_record: boolean;
      days_with_check_in: number;
      check_ins: number;
      check_outs: number;
      timenex_confirmed_check_ins: number;
      timenex_mapping_label: string;
    }>;
    timenex_confirmed: number;
  } | null>(null);
  const [error, setError] = useState("");

  async function load(event?: FormEvent) {
    event?.preventDefault();
    setError("");
    try {
      setData(await api(`/api/admin/attendance/month?month=${month}`));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load the monthly sheet.");
    }
  }

  useEffect(() => {
    load().catch(() => undefined);
  }, []);

  return (
    <section className="card p-4">
      <form className="flex flex-wrap items-end justify-between gap-3" onSubmit={load}>
        <div>
          <h2 className="text-lg font-semibold">Month · {data?.month || month}</h2>
          <p className="mt-1 text-sm text-slate-600">Days with a check-in in this application. A zero is not an absence. Confirmed by TimeNex: {data?.timenex_confirmed ?? 0}.</p>
        </div>
        <div>
          <label className="label" htmlFor="month">Month</label>
          <input id="month" className="field" type="month" value={month} onChange={(e) => setMonth(e.target.value)} />
        </div>
        <button className="btn-secondary" type="submit">Show month</button>
      </form>
      {error && <p className="mt-3 text-sm text-red-800">{error}</p>}
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <article className="rounded-2xl bg-emerald-50 p-3"><p className="text-sm text-slate-600">With a check-in</p><p className="text-2xl font-semibold">{data?.employees_with_check_in ?? "—"}</p></article>
        <article className="rounded-2xl bg-slate-100 p-3"><p className="text-sm text-slate-600">No check-in this month</p><p className="text-2xl font-semibold">{data?.employees_without_check_in ?? "—"}</p></article>
      </div>
      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[720px] text-left text-sm">
          <thead className="text-slate-500"><tr><th className="py-2">Name</th><th>Employee ID</th><th>Department</th><th>Days</th><th>Check-ins</th><th>Check-outs</th><th>TimeNex link</th><th>TimeNex confirmed</th></tr></thead>
          <tbody>
            {(data?.rows || []).map((row) => (
              <tr key={row.employee_number} className="border-t border-slate-100">
                <td className="py-2 font-medium">{row.employee_name}{row.is_test_record ? " · TEST" : ""}</td>
                <td>{row.employee_number}</td>
                <td>{row.department || "—"}</td>
                <td>{row.days_with_check_in}</td>
                <td>{row.check_ins}</td>
                <td>{row.check_outs}</td>
                <td>{row.timenex_mapping_label || "Not mapped"}</td>
                <td>{row.timenex_confirmed_check_ins}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

type CorrectionRow = {
  id: string;
  employee_name: string;
  employee_number: string | null;
  kind_label: string;
  status: string;
  status_label: string;
  reason: string;
  timenex_mapping_label: string;
  timenex_confirmed: boolean;
  created_at: { pkt_display: string };
};

function Corrections() {
  const [rows, setRows] = useState<CorrectionRow[]>([]);
  const [recent, setRecent] = useState<CorrectionRow[]>([]);
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");

  async function load() {
    const [pending, all] = await Promise.all([
      api<{ requests: CorrectionRow[] }>("/api/admin/corrections?status=PENDING"),
      api<{ requests: CorrectionRow[] }>("/api/admin/corrections"),
    ]);
    setRows(pending.requests);
    setRecent(all.requests.filter((row) => row.status !== "PENDING").slice(0, 8));
  }
  useEffect(() => { load().catch((err: unknown) => setError(err instanceof Error ? err.message : "Could not load corrections.")); }, []);

  async function review(id: string, action: "approve" | "reject") {
    setError("");
    try {
      await api(`/api/admin/corrections/${id}/${action}`, { method: "POST", body: JSON.stringify({ review_reason: reason }) });
      setReason("");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Review failed.");
    }
  }

  return (
    <section className="card space-y-3 p-4">
      <h2 className="text-lg font-semibold">Correction requests</h2>
      <p className="text-sm text-slate-600">Approval adds an administrator correction in this application. It does not delete the earlier record, and it is not sent to TimeNex.</p>
      <label className="label" htmlFor="review">Review reason for the next decision</label>
      <textarea id="review" className="field min-h-24" value={reason} onChange={(e) => setReason(e.target.value)} />
      {error && <p className="text-sm text-red-800">{error}</p>}
      {rows.map((row) => (
        <article key={row.id} className="rounded-xl bg-brand-50 p-3 text-sm">
          <p className="font-medium">{row.employee_name} · {row.employee_number} · {row.kind_label}</p>
          <p>{row.reason}</p>
          <p className="mt-1"><TimenexBadge label={row.timenex_confirmed ? "Confirmed by TimeNex" : "Not confirmed by TimeNex"} confirmed={row.timenex_confirmed} /></p>
          <div className="mt-2 flex gap-2">
            <button className="btn-primary" type="button" onClick={() => review(row.id, "approve")}>Approve</button>
            <button className="btn-secondary" type="button" onClick={() => review(row.id, "reject")}>Reject</button>
          </div>
        </article>
      ))}
      {rows.length === 0 && <p className="text-sm text-slate-600">No pending correction requests.</p>}
      {recent.length > 0 && (
        <div className="border-t border-slate-100 pt-3">
          <h3 className="font-medium">Earlier decisions</h3>
          <ul className="mt-2 space-y-2">
            {recent.map((row) => (
              <li key={row.id} className="text-sm">
                {row.created_at.pkt_display} · {row.employee_name} · {row.status_label} · <TimenexBadge label={row.timenex_confirmed ? "Confirmed by TimeNex" : "Not confirmed by TimeNex"} confirmed={row.timenex_confirmed} />
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function Audit() {
  const [rows, setRows] = useState<Array<{ id: string; action: string; summary: string; created_at: { pkt_display: string } }>>([]);
  useEffect(() => {
    api<{ entries: typeof rows }>("/api/admin/audit").then((data) => setRows(data.entries)).catch(() => undefined);
  }, []);
  return (
    <section className="card p-4">
      <h2 className="text-lg font-semibold">Audit history</h2>
      <p className="mt-1 text-sm text-slate-600">These entries stay in this application. A mapping or check-in listed here was not sent to TimeNex.</p>
      <ul className="mt-3 divide-y text-sm">
        {rows.map((row) => <li key={row.id} className="py-2">{row.created_at.pkt_display} · {row.summary || row.action}</li>)}
        {rows.length === 0 && <li className="py-2 text-slate-600">No audit entries yet.</li>}
      </ul>
    </section>
  );
}

function Devices() {
  const [devices, setDevices] = useState<Array<{ id: string; name: string; model: string; serial_number: string; status_label: string; receiving_events: boolean; notes: string | null }>>([]);
  const [links, setLinks] = useState<Array<{ code: string; display_name: string; status_label: string; summary: string; synced: boolean }>>([]);
  const [note, setNote] = useState("");
  useEffect(() => {
    Promise.all([
      api<{ devices: typeof devices }>("/api/admin/devices"),
      api<{ note: string; integrations: typeof links }>("/api/admin/integrations"),
    ]).then(([deviceData, integrations]) => {
      setDevices(deviceData.devices);
      setLinks(integrations.integrations);
      setNote(integrations.note);
    }).catch(() => undefined);
  }, []);
  return (
    <div className="space-y-3">
      <p className="rounded-xl bg-brand-100 px-3 py-2 text-sm">{note || "Loading integration status…"}</p>
      {links.map((link) => (
        <article key={link.code} className="card p-4 text-sm">
          <h2 className="text-lg font-semibold">{link.display_name}</h2>
          <p>{link.status_label}</p>
          <p className="mt-1 text-slate-600">{link.summary}</p>
          <p className="mt-1">Confirmed attendance from this connection: {link.synced ? "Yes" : "No"}.</p>
        </article>
      ))}
      {devices.map((device) => (
        <article key={device.id} className="card p-4 text-sm">
          <h2 className="text-lg font-semibold">{device.name}</h2>
          <p>{device.model} · {device.serial_number}</p>
          <p>{device.status_label} Last seen: never.</p>
          <p className="mt-2 text-slate-600">{device.notes}</p>
        </article>
      ))}
      <ImportBox />
      <EmployeeImport />
    </div>
  );
}

function ImportBox() {
  const [file, setFile] = useState<File | null>(null);
  const [reason, setReason] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file) return;
    setError("");
    const body = new FormData();
    body.set("file", file);
    body.set("reason", reason);
    body.set("confirmation", confirmation);
    try {
      const result = await api<{ imported: number; flagged: number; skipped_duplicates: number; message: string }>("/api/admin/imports/attendance", { method: "POST", body });
      setMessage(`${result.message} Imported ${result.imported}, flagged ${result.flagged}, skipped ${result.skipped_duplicates}.`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Import failed.");
    }
  }

  return (
    <form className="card space-y-3 p-4" onSubmit={submit}>
      <h2 className="text-lg font-semibold">Manual CSV import</h2>
      <p className="text-sm text-slate-600">This accepts this application's template only. Imported rows stay here. The file does not connect to the terminal and it is not sent to TimeNex.</p>
      <a className="text-sm font-medium text-brand-700 underline" href="/api/admin/imports/template.csv">Download template</a>
      <input className="block text-sm" type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files?.[0] || null)} />
      <textarea className="field min-h-20" placeholder="Reason for this import" value={reason} onChange={(e) => setReason(e.target.value)} />
      <input className="field" placeholder="Type AUTHORIZED EXPORT" value={confirmation} onChange={(e) => setConfirmation(e.target.value)} />
      {error && <p className="text-sm text-red-800">{error}</p>}
      {message && <p className="text-sm text-green-800">{message}</p>}
      <button className="btn-primary" type="submit">Import</button>
    </form>
  );
}

function EmployeeImport() {
  const [file, setFile] = useState<File | null>(null);
  const [reason, setReason] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file) return;
    setError("");
    const body = new FormData();
    body.set("file", file);
    body.set("reason", reason);
    body.set("confirmation", confirmation);
    try {
      const result = await api<{ imported: number; message: string }>("/api/admin/imports/employees", { method: "POST", body });
      setMessage(`${result.message} Created ${result.imported}.`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Import failed.");
    }
  }

  return (
    <form className="card space-y-3 p-4" onSubmit={submit}>
      <h2 className="text-lg font-semibold">Authorized employee CSV</h2>
      <p className="text-sm text-slate-600">Creates manager accounts from a file you already have. Duplicate employee numbers and emails are rejected. The file is not fetched from TimeNex.</p>
      <a className="text-sm font-medium text-brand-700 underline" href="/api/admin/imports/employees-template.csv">Download employee template</a>
      <input className="block text-sm" type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files?.[0] || null)} />
      <textarea className="field min-h-20" placeholder="Reason for this import" value={reason} onChange={(e) => setReason(e.target.value)} />
      <input className="field" placeholder="Type AUTHORIZED EXPORT" value={confirmation} onChange={(e) => setConfirmation(e.target.value)} />
      {error && <p className="text-sm text-red-800">{error}</p>}
      {message && <p className="text-sm text-green-800">{message}</p>}
      <button className="btn-primary" type="submit">Import employees</button>
    </form>
  );
}

function QaHarness() {
  const [employees, setEmployees] = useState("1");
  const [pairs, setPairs] = useState("1");
  const [confirmation, setConfirmation] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      const result = await api<{ events: number; employee_numbers: string[]; message: string }>("/api/admin/harness/run", {
        method: "POST",
        body: JSON.stringify({ employees: Number(employees), pairs: Number(pairs), confirmation }),
      });
      setMessage(`${result.message} Events: ${result.events}. Numbers: ${result.employee_numbers.join(", ")}.`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The harness did not run.");
    }
  }

  return (
    <form className="card space-y-3 p-4" onSubmit={submit}>
      <h2 className="text-lg font-semibold">QA harness</h2>
      <p className="text-sm text-slate-600">Creates synthetic employees and closed check-in/check-out pairs labelled QA test data. Those rows stay in this database. They are not official attendance and are not sent anywhere.</p>
      <div className="flex flex-wrap gap-3">
        <div>
          <label className="label" htmlFor="qa-employees">Employees</label>
          <input id="qa-employees" className="field" inputMode="numeric" value={employees} onChange={(e) => setEmployees(e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="qa-pairs">Closed pairs each</label>
          <input id="qa-pairs" className="field" inputMode="numeric" value={pairs} onChange={(e) => setPairs(e.target.value)} />
        </div>
      </div>
      <input className="field" placeholder="Type RUN QA HARNESS" value={confirmation} onChange={(e) => setConfirmation(e.target.value)} />
      {error && <p className="text-sm text-red-800">{error}</p>}
      {message && <p className="text-sm text-green-800">{message}</p>}
      <button className="btn-primary" type="submit">Create test data</button>
    </form>
  );
}

const WEEK = [
  ["MON", "Mon"],
  ["TUE", "Tue"],
  ["WED", "Wed"],
  ["THU", "Thu"],
  ["FRI", "Fri"],
  ["SAT", "Sat"],
  ["SUN", "Sun"],
] as const;

function Policy() {
  const [policy, setPolicy] = useState<{ remote_attendance_approved: boolean; passkey_required_for_attendance: boolean; approval_phrase: string; revoke_phrase: string; passkey_phrase: string; password_attendance_phrase: string; location_policy: string; policy_note: string; inference_enabled: boolean; enforce_working_days: boolean; working_days: string[]; work_start_local: string | null; work_end_local: string | null; grace_minutes: number | null } | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [passkeyConfirmation, setPasskeyConfirmation] = useState("");
  const [days, setDays] = useState<string[]>(["MON", "TUE", "WED", "THU", "FRI"]);
  const [start, setStart] = useState("09:00");
  const [end, setEnd] = useState("17:00");
  const [grace, setGrace] = useState("15");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api<NonNullable<typeof policy>>("/api/admin/policy").then((data) => {
      setPolicy(data);
      setDays(data.working_days.length ? data.working_days : ["MON", "TUE", "WED", "THU", "FRI"]);
      setStart(data.work_start_local || "09:00");
      setEnd(data.work_end_local || "17:00");
      setGrace(data.grace_minutes === null ? "15" : String(data.grace_minutes));
    }).catch(() => undefined);
  }, []);

  function toggleDay(code: string) {
    setDays((current) => current.includes(code) ? current.filter((day) => day !== code) : [...current, code]);
  }

  async function saveTimings(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      const next = await api<NonNullable<typeof policy>>("/api/admin/policy", {
        method: "PATCH",
        body: JSON.stringify({
          working_days: days,
          work_start_local: start,
          work_end_local: end,
          grace_minutes: grace === "" ? null : Number(grace),
          enforce_working_days: false,
          inference_enabled: false,
        }),
      });
      setPolicy(next);
      setMessage(`Office timings saved: ${next.work_start_local}–${next.work_end_local} PKT. Late and early status stays off.`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save office timings.");
    }
  }

  async function changeRemote(event: FormEvent) {
    event.preventDefault();
    if (!policy) return;
    setError("");
    try {
      const next = await api<NonNullable<typeof policy>>("/api/admin/policy", {
        method: "PATCH",
        body: JSON.stringify({
          remote_attendance_approved: !policy.remote_attendance_approved,
          confirmation,
        }),
      });
      setPolicy(next);
      setConfirmation("");
      setMessage(next.remote_attendance_approved ? "Remote attendance is on." : "Remote attendance is off.");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not change remote attendance.");
    }
  }

  async function changePasskey(event: FormEvent) {
    event.preventDefault();
    if (!policy) return;
    setError("");
    setMessage("");
    try {
      const next = await api<NonNullable<typeof policy>>("/api/admin/policy", {
        method: "PATCH",
        body: JSON.stringify({
          passkey_required_for_attendance: !policy.passkey_required_for_attendance,
          confirmation: passkeyConfirmation,
        }),
      });
      setPolicy(next);
      setPasskeyConfirmation("");
      setMessage(next.passkey_required_for_attendance ? "Attendance now requires a passkey." : "Password sessions can mark attendance.");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not change the passkey rule.");
    }
  }

  if (!policy) return <p>Loading office timings…</p>;
  return (
    <div className="space-y-4">
      <form className="card space-y-3 p-4" onSubmit={saveTimings}>
        <h2 className="text-lg font-semibold">Office timings</h2>
        <p className="text-sm text-slate-600">Times use 24-hour Pakistan time. Saving a schedule does not calculate late or early attendance, and it does not block a day.</p>
        <div className="flex flex-wrap gap-2">
          {WEEK.map(([code, label]) => (
            <label key={code} className={`rounded-full px-3 py-2 text-sm font-medium ${days.includes(code) ? "bg-[#071a33] text-white" : "bg-slate-100 text-slate-700"}`}>
              <input className="sr-only" type="checkbox" checked={days.includes(code)} onChange={() => toggleDay(code)} />
              {label}
            </label>
          ))}
        </div>
        <div className="grid gap-3 md:grid-cols-3">
          <div><label className="label" htmlFor="work-start">Work start</label><input id="work-start" className="field" value={start} onChange={(e) => setStart(e.target.value)} placeholder="09:00" /></div>
          <div><label className="label" htmlFor="work-end">Work end</label><input id="work-end" className="field" value={end} onChange={(e) => setEnd(e.target.value)} placeholder="17:00" /></div>
          <div><label className="label" htmlFor="grace">Grace minutes</label><input id="grace" className="field" inputMode="numeric" value={grace} onChange={(e) => setGrace(e.target.value)} /></div>
        </div>
        <button className="btn-primary" type="submit">Save office timings</button>
      </form>
      <form className="card space-y-3 p-4" onSubmit={changeRemote}>
        <h2 className="text-lg font-semibold">Remote attendance</h2>
        <p className="text-sm">{policy.policy_note}</p>
        <p className="text-sm font-medium">Remote attendance is {policy.remote_attendance_approved ? "on" : "off"}.</p>
        <p className="text-sm text-slate-600">{policy.location_policy}</p>
        <p className="text-sm text-slate-600">To change it, type {policy.remote_attendance_approved ? policy.revoke_phrase : policy.approval_phrase}.</p>
        <input className="field" aria-label="Remote attendance confirmation" value={confirmation} onChange={(e) => setConfirmation(e.target.value)} />
        <button className="btn-secondary" type="submit">{policy.remote_attendance_approved ? "Revoke remote attendance" : "Approve remote attendance"}</button>
      </form>
      <form className="card space-y-3 p-4" onSubmit={changePasskey}>
        <h2 className="text-lg font-semibold">Passkey for attendance</h2>
        <p className="text-sm text-slate-600">The confirmed rule is that check-in and check-out require a passkey with user verification. Windows Hello, Android, Face ID, Touch ID, another device, or a security key can satisfy it. The server stores a public key only. A passkey does not prove location.</p>
        <p className="text-sm font-medium">Passkey requirement is {policy.passkey_required_for_attendance ? "on" : "off"}.</p>
        <p className="text-sm text-slate-600">To change it, type {policy.passkey_required_for_attendance ? policy.password_attendance_phrase : policy.passkey_phrase}.</p>
        <input className="field" aria-label="Passkey attendance confirmation" value={passkeyConfirmation} onChange={(e) => setPasskeyConfirmation(e.target.value)} />
        <button className="btn-secondary" type="submit">{policy.passkey_required_for_attendance ? "Allow password-session attendance" : "Require a passkey"}</button>
      </form>
      {error && <p className="text-sm text-red-800">{error}</p>}
      {message && <p className="text-sm text-green-800">{message}</p>}
    </div>
  );
}

function ErpInternalId({
  mappingId,
  current,
  reason,
  onSaved,
}: {
  mappingId: string;
  current: string;
  reason: string;
  onSaved: (message: string) => void;
}) {
  const [value, setValue] = useState(current);
  const [error, setError] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      const saved = await api<{ message: string }>(`/api/admin/timenex/mappings/${mappingId}/erp-id`, {
        method: "POST",
        body: JSON.stringify({ erp_employee_id: value, reason }),
      });
      onSaved(saved.message);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The ERP internal ID was not stored.");
    }
  }
  return (
    <form className="flex flex-wrap items-center gap-2" onSubmit={submit}>
      <input className="field max-w-[8rem]" aria-label={`ERP internal ID for ${mappingId}`} placeholder="ERP key" value={value} onChange={(e) => setValue(e.target.value)} required />
      <button className="btn-secondary" type="submit">Save</button>
      {error && <span className="text-red-800">{error}</span>}
    </form>
  );
}

function ReplaceMapping({ employeeNumber, onSaved }: { employeeNumber: string; onSaved: () => void }) {
  const [nextId, setNextId] = useState("");
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      await api("/api/admin/timenex/mappings", {
        method: "POST",
        body: JSON.stringify({ employee_number: employeeNumber, timenex_employee_id: nextId }),
      });
      setNextId("");
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The link was not replaced.");
    }
  }

  return (
    <form className="flex flex-wrap items-center gap-2" onSubmit={submit}>
      <input className="field max-w-[10rem]" aria-label={`New TimeNex ID for ${employeeNumber}`} placeholder="New TimeNex ID" value={nextId} onChange={(e) => setNextId(e.target.value)} required />
      <button className="btn-secondary" type="submit">Replace</button>
      {error && <span className="text-red-800">{error}</span>}
    </form>
  );
}

type TimenexRow = {
  id: string;
  local_employee_number: string | null;
  timenex_employee_id: string;
  erp_employee_id?: string | null;
  employee_name: string;
  department_name: string;
  account_status: string;
  verification_status: string;
};

type OutboxRow = {
  id: string;
  employee_number: string;
  employee_name: string;
  event_type: string;
  source_label: string;
  status_label: string;
  erp_status_label?: string;
  confirmed: boolean;
  attempt_count: number;
  effective_at: { pkt_display: string };
};

type SyncCounts = {
  local_recorded: number;
  attention_count: number;
  erp_pending_count: number;
  erp_accepted_count: number;
  timenex_pending_count: number;
  confirmed_count: number;
};

type RosterRow = {
  line: number;
  timenex_employee_id: string;
  full_name: string;
  department_name: string;
  designation: string;
  export_status: string;
  result: string;
  message: string;
};

type RosterPreview = {
  used_columns: string[];
  ignored_columns: string[];
  attendance_note: string;
  total: number;
  ready: number;
  already_stored: number;
  missing_department: number;
  blocked: number;
  rows: RosterRow[];
};

const ROSTER_RESULT: Record<string, string> = {
  READY: "Ready",
  ALREADY_STORED: "Already stored",
  DUPLICATE_IN_FILE: "Duplicate ID",
  INCOMPLETE: "Incomplete",
  INCONSISTENT: "Inconsistent",
};

function TimenexEmployeeImport({ onImported }: { onImported: () => Promise<void> }) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<RosterPreview | null>(null);
  const [reason, setReason] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  async function review(event: FormEvent) {
    event.preventDefault();
    if (!file) return;
    setError("");
    setMessage("");
    setPreview(null);
    const body = new FormData();
    body.set("file", file);
    try {
      const result = await api<RosterPreview>("/api/admin/timenex/employees/preview", { method: "POST", body });
      setPreview(result);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The file could not be reviewed.");
    }
  }

  async function store(event: FormEvent) {
    event.preventDefault();
    if (!file || !preview || preview.blocked > 0 || preview.ready === 0) return;
    setError("");
    setMessage("");
    const body = new FormData();
    body.set("file", file);
    body.set("reason", reason);
    body.set("confirmation", confirmation);
    try {
      const result = await api<{ imported: number; already_stored: number; message: string }>(
        "/api/admin/timenex/employees/import",
        { method: "POST", body },
      );
      setMessage(result.message);
      setPreview(null);
      setReason("");
      setConfirmation("");
      await onImported();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The employees were not stored.");
    }
  }

  return (
    <section className="card space-y-3 p-4">
      <h2 className="text-lg font-semibold">TimeNex employee file</h2>
      <p className="text-sm text-slate-600">
        Review an authorized TimeNex employee export before anything is stored. The ID, name, department, and designation are kept. The TimeNex ID is not a password. In, Out, and Status stay out of attendance.
      </p>
      <form className="flex flex-wrap items-center gap-3" onSubmit={review}>
        <input
          className="block text-sm"
          type="file"
          accept=".csv,text/csv"
          aria-label="TimeNex employee export"
          onChange={(e) => {
            setFile(e.target.files?.[0] || null);
            setPreview(null);
          }}
        />
        <button className="btn-secondary" type="submit" disabled={!file}>Review</button>
      </form>
      {preview && (
        <form className="space-y-3" onSubmit={store}>
          <p className="text-sm text-slate-600">{preview.attendance_note}</p>
          <p className="text-sm">
            {preview.total} rows · {preview.ready} ready · {preview.already_stored} already stored · {preview.missing_department} without a department · {preview.blocked} need attention
          </p>
          <p className="text-sm text-slate-600">Columns used: {preview.used_columns.join(", ")}. Left out: {preview.ignored_columns.join(", ") || "none"}.</p>
          <div className="max-h-80 overflow-auto">
            <table className="min-w-full text-left text-sm">
              <thead>
                <tr className="border-b text-slate-500">
                  <th className="py-2 pr-3">Line</th>
                  <th className="py-2 pr-3">TimeNex ID</th>
                  <th className="py-2 pr-3">Name</th>
                  <th className="py-2 pr-3">Department</th>
                  <th className="py-2 pr-3">Designation</th>
                  <th className="py-2 pr-3">Export status</th>
                  <th className="py-2">Review</th>
                </tr>
              </thead>
              <tbody>
                {preview.rows.map((row) => (
                  <tr key={`${row.line}-${row.timenex_employee_id}`} className="border-b">
                    <td className="py-2 pr-3">{row.line}</td>
                    <td className="py-2 pr-3">{row.timenex_employee_id}</td>
                    <td className="py-2 pr-3">{row.full_name}</td>
                    <td className="py-2 pr-3">{row.department_name || "Unassigned"}</td>
                    <td className="py-2 pr-3">{row.designation}</td>
                    <td className="py-2 pr-3">{row.export_status || "—"}</td>
                    <td className="py-2">{ROSTER_RESULT[row.result] || row.result}. {row.message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <textarea className="field min-h-20" placeholder="Reason for storing this export" value={reason} onChange={(e) => setReason(e.target.value)} required />
          <input className="field" placeholder="Type AUTHORIZED EXPORT" value={confirmation} onChange={(e) => setConfirmation(e.target.value)} required />
          <button className="btn-primary" type="submit" disabled={preview.blocked > 0 || preview.ready === 0}>
            Store reviewed employees
          </button>
          {preview.blocked > 0 && <p className="text-sm text-red-800">Fix the rows that need attention before storing. Nothing has been saved.</p>}
        </form>
      )}
      {error && <p className="text-sm text-red-800" role="alert">{error}</p>}
      {message && <p className="text-sm text-green-800">{message}</p>}
    </section>
  );
}

function TimenexMapping() {
  const [rows, setRows] = useState<TimenexRow[]>([]);
  const [queue, setQueue] = useState<OutboxRow[]>([]);
  const [queueNote, setQueueNote] = useState("");
  const [confirmedCount, setConfirmedCount] = useState(0);
  const [counts, setCounts] = useState<SyncCounts | null>(null);
  const [note, setNote] = useState("");
  const [employeeNumber, setEmployeeNumber] = useState("");
  const [timenexId, setTimenexId] = useState("");
  const [reason, setReason] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [importReason, setImportReason] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  async function load() {
    const [data, outbox] = await Promise.all([
      api<{ mappings: TimenexRow[]; note: string }>("/api/admin/timenex/mappings"),
      api<{ items: OutboxRow[]; note: string } & SyncCounts>("/api/admin/timenex/outbox"),
    ]);
    setRows(data.mappings);
    setNote(data.note);
    setQueue(outbox.items);
    setQueueNote(outbox.note);
    setConfirmedCount(outbox.confirmed_count);
    setCounts(outbox);
  }

  useEffect(() => {
    load().catch((err: unknown) => setError(err instanceof ApiError ? err.message : "Could not load mappings."));
  }, []);

  async function create(event: FormEvent) {
    event.preventDefault();
    setError("");
    setMessage("");
    try {
      await api("/api/admin/timenex/mappings", {
        method: "POST",
        body: JSON.stringify({ employee_number: employeeNumber, timenex_employee_id: timenexId }),
      });
      setEmployeeNumber("");
      setTimenexId("");
      setMessage("Mapping saved as pending. It does not sign the employee in, and it does not send attendance.");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The mapping was not saved.");
    }
  }

  async function decide(id: string, decision: "APPROVE" | "REJECT") {
    setError("");
    setMessage("");
    try {
      await api(`/api/admin/timenex/mappings/${id}/decision`, {
        method: "POST",
        body: JSON.stringify({ decision, reason }),
      });
      setMessage(decision === "APPROVE" ? "Mapping verified. TimeNex has still not confirmed any attendance." : "Mapping rejected.");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The decision was not saved.");
    }
  }

  async function importFile(event: FormEvent) {
    event.preventDefault();
    if (!file) return;
    setError("");
    setMessage("");
    const body = new FormData();
    body.set("file", file);
    body.set("reason", importReason);
    body.set("confirmation", confirmation);
    try {
      const result = await api<{ created: number; message: string }>("/api/admin/timenex/mappings/import", {
        method: "POST",
        body,
      });
      setMessage(`${result.message} Verified ${result.created}.`);
      setFile(null);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The file was not imported.");
    }
  }

  return (
    <div className="space-y-4">
      <TimenexEmployeeImport onImported={load} />
      <section className="card space-y-2 p-4">
        <h2 className="text-lg font-semibold">TimeNex employee mapping</h2>
        <p className="text-sm text-slate-600">
          Link a person already stored in this application to a TimeNex employee ID. The ID is not a password. TimeNex is the attendance destination. The ERP attendance page is not used. A check-in stays here until TimeNex confirms it through an authorized interface. That confirmation is not available yet.
        </p>
        {note && <p className="text-sm text-slate-600">{note}</p>}
      </section>
      <section className="card space-y-2 p-4">
        <h2 className="text-lg font-semibold">TimeNex queue</h2>
        <p className="text-sm text-slate-600">{queueNote || "TimeNex has not confirmed attendance from this application."}</p>
        <p className="text-sm">
          Local recorded: {counts?.local_recorded ?? 0}. TimeNex synchronization pending: {counts?.timenex_pending_count ?? 0}. TimeNex synchronization confirmed: {confirmedCount}. Needs attention: {counts?.attention_count ?? 0}. The ERP page is not the integration path.
        </p>
        <div className="overflow-x-auto">
          <table className="min-w-full text-left text-sm">
            <thead>
              <tr className="border-b text-slate-500">
                <th className="py-2 pr-3">Person</th>
                <th className="py-2 pr-3">Event</th>
                <th className="py-2 pr-3">When</th>
                <th className="py-2 pr-3">Source</th>
                <th className="py-2 pr-3">ERP</th>
                <th className="py-2">TimeNex</th>
              </tr>
            </thead>
            <tbody>
              {queue.map((item) => (
                <tr key={item.id} className="border-b">
                  <td className="py-2 pr-3">{item.employee_name} · {item.employee_number}</td>
                  <td className="py-2 pr-3">{item.event_type === "CHECK_IN" ? "Check-in" : "Check-out"}</td>
                  <td className="py-2 pr-3">{item.effective_at.pkt_display}</td>
                  <td className="py-2 pr-3">{item.source_label}</td>
                  <td className="py-2 pr-3">{item.erp_status_label || "—"}</td>
                  <td className="py-2">{item.status_label}</td>
                </tr>
              ))}
              {queue.length === 0 && <tr><td className="py-3 text-slate-500" colSpan={6}>No attendance is waiting in the queue.</td></tr>}
            </tbody>
          </table>
        </div>
      </section>
      <form className="card grid gap-3 p-4 md:grid-cols-3" onSubmit={create}>
        <div>
          <label className="label" htmlFor="local-number">Local employee number</label>
          <input id="local-number" className="field" value={employeeNumber} onChange={(e) => setEmployeeNumber(e.target.value)} required />
        </div>
        <div>
          <label className="label" htmlFor="timenex-id">TimeNex employee ID</label>
          <input id="timenex-id" className="field" value={timenexId} onChange={(e) => setTimenexId(e.target.value)} required />
        </div>
        <div className="flex items-end">
          <button className="btn-primary w-full" type="submit">Save pending mapping</button>
        </div>
      </form>
      <div className="card space-y-3 p-4">
        <label className="label" htmlFor="map-reason">Approval reason</label>
        <input id="map-reason" className="field" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="At least 10 characters" />
        <div className="overflow-x-auto">
          <table className="min-w-full text-left text-sm">
            <thead>
              <tr className="border-b text-slate-500">
                <th className="py-2 pr-3">Name</th>
                <th className="py-2 pr-3">Local ID</th>
                <th className="py-2 pr-3">TimeNex ID</th>
                <th className="py-2 pr-3">ERP internal ID</th>
                <th className="py-2 pr-3">Department</th>
                <th className="py-2 pr-3">Account</th>
                <th className="py-2 pr-3">Verification</th>
                <th className="py-2">Action</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} className="border-b">
                  <td className="py-2 pr-3">{row.employee_name}</td>
                  <td className="py-2 pr-3">{row.local_employee_number}</td>
                  <td className="py-2 pr-3">{row.timenex_employee_id}</td>
                  <td className="py-2 pr-3">
                    {row.verification_status === "VERIFIED" ? (
                      <ErpInternalId
                        mappingId={row.id}
                        current={row.erp_employee_id || ""}
                        reason={reason}
                        onSaved={(text) => {
                          setMessage(text);
                          load().catch(() => undefined);
                        }}
                      />
                    ) : "—"}
                  </td>
                  <td className="py-2 pr-3">{row.department_name}</td>
                  <td className="py-2 pr-3">{row.account_status}</td>
                  <td className="py-2 pr-3">{row.verification_status}</td>
                  <td className="py-2">
                    {row.verification_status === "PENDING" && (
                      <span className="flex gap-2">
                        <button className="btn-primary" type="button" onClick={() => decide(row.id, "APPROVE")}>Verify</button>
                        <button className="btn-secondary" type="button" onClick={() => decide(row.id, "REJECT")}>Reject</button>
                      </span>
                    )}
                    {row.verification_status === "REJECTED" && row.local_employee_number && (
                      <ReplaceMapping
                        employeeNumber={row.local_employee_number}
                        onSaved={() => {
                          setMessage("The rejected link was replaced with a pending one. The earlier rejection stays in the audit history. Nothing was sent to TimeNex.");
                          load().catch(() => undefined);
                        }}
                      />
                    )}
                  </td>
                </tr>
              ))}
              {rows.length === 0 && <tr><td className="py-3 text-slate-500" colSpan={8}>No mappings yet.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
      <form className="card space-y-3 p-4" onSubmit={importFile}>
        <h2 className="text-lg font-semibold">Authorized mapping file</h2>
        <p className="text-sm text-slate-600">Use a file you already have. The name and department must match the person stored here. The file is not fetched from TimeNex, and importing it does not send attendance.</p>
        <a className="text-sm font-medium text-brand-700 underline" href="/api/admin/timenex/mappings-template.csv">Download mapping template</a>
        <input className="block text-sm" type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files?.[0] || null)} />
        <textarea className="field min-h-20" placeholder="Reason for this import" value={importReason} onChange={(e) => setImportReason(e.target.value)} />
        <input className="field" placeholder="Type AUTHORIZED EXPORT" value={confirmation} onChange={(e) => setConfirmation(e.target.value)} />
        <button className="btn-primary" type="submit">Verify from file</button>
      </form>
      {error && <p className="text-sm text-red-800" role="alert">{error}</p>}
      {message && <p className="text-sm text-green-800">{message}</p>}
    </div>
  );
}
