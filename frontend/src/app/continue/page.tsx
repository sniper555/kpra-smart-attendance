"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError, setCsrf } from "@/lib/api";

type Challenge = { status: string };

export default function ContinuePage() {
  const router = useRouter();
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [password, setPassword] = useState("");
  const [secret, setSecret] = useState("");
  const [qr, setQr] = useState("");
  const [code, setCode] = useState("");
  const [recovery, setRecovery] = useState<string[]>([]);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api<Challenge>("/api/auth/challenge")
      .then((data) => setStatus(data.status))
      .catch(() => router.replace("/login"));
  }, [router]);

  async function startEnroll() {
    const data = await api<{ secret: string; qr_svg_base64: string }>("/api/auth/mfa/enroll/start", { method: "POST" });
    setSecret(data.secret);
    setQr(data.qr_svg_base64);
  }

  useEffect(() => {
    if (status === "enrollment_required") {
      startEnroll().catch((err: unknown) => setError(err instanceof Error ? err.message : "Could not start enrollment."));
    }
  }, [status]);

  async function submitPassword(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const data = await api<Challenge>("/api/auth/password", {
        method: "POST",
        body: JSON.stringify({ new_password: password }),
      });
      setStatus(data.status);
      setPassword("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not change the password.");
    } finally {
      setBusy(false);
    }
  }

  async function submitCode(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const path = status === "enrollment_required" ? "/api/auth/mfa/enroll/confirm" : "/api/auth/mfa/verify";
      const data = await api<{ csrf_token: string; recovery_codes?: string[] }>(path, {
        method: "POST",
        body: JSON.stringify({ code }),
      });
      setCsrf(data.csrf_token);
      if (data.recovery_codes) {
        setRecovery(data.recovery_codes);
      } else {
        router.replace("/home");
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The code was not accepted.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main id="main" className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-4 py-10">
      <div className="card p-6">
        <h1 className="text-2xl font-semibold">Finish signing in</h1>
        {status === "password_change_required" && (
          <form className="mt-4 space-y-4" onSubmit={submitPassword}>
            <p className="text-sm text-slate-600">Choose a new password before MFA. Use at least 12 characters with a letter and a number.</p>
            <label className="label" htmlFor="new-password">New password</label>
            <input id="new-password" className="field" type="password" autoComplete="new-password" required value={password} onChange={(event) => setPassword(event.target.value)} />
            <button className="btn-primary w-full" disabled={busy} type="submit">{busy ? "Saving…" : "Save password"}</button>
          </form>
        )}
        {status === "enrollment_required" && recovery.length === 0 && (
          <form className="mt-4 space-y-4" onSubmit={submitCode}>
            <p className="text-sm text-slate-600">Add this account to an authenticator app, then enter the 6-digit code.</p>
            {qr && <img alt="QR code for authenticator app" className="mx-auto h-48 w-48" src={`data:image/svg+xml;base64,${qr}`} />}
            <p className="break-all rounded-xl bg-brand-50 px-3 py-2 font-mono text-sm">{secret}</p>
            <label className="label" htmlFor="enroll-code">Authentication code</label>
            <input id="enroll-code" className="field" inputMode="numeric" autoComplete="one-time-code" required value={code} onChange={(event) => setCode(event.target.value)} />
            <button className="btn-primary w-full" disabled={busy} type="submit">{busy ? "Checking…" : "Enable MFA"}</button>
          </form>
        )}
        {status === "mfa_required" && (
          <form className="mt-4 space-y-4" onSubmit={submitCode}>
            <p className="text-sm text-slate-600">Enter the code from your authenticator app, or a recovery code.</p>
            <label className="label" htmlFor="mfa-code">Code</label>
            <input id="mfa-code" className="field" autoComplete="one-time-code" required value={code} onChange={(event) => setCode(event.target.value)} />
            <button className="btn-primary w-full" disabled={busy} type="submit">{busy ? "Checking…" : "Verify"}</button>
          </form>
        )}
        {recovery.length > 0 && (
          <div className="mt-4 space-y-4">
            <p className="text-sm text-slate-700">Save these recovery codes now. They are shown once.</p>
            <ul className="grid grid-cols-2 gap-2 font-mono text-sm">
              {recovery.map((item) => <li key={item} className="rounded-lg bg-brand-50 px-2 py-1">{item}</li>)}
            </ul>
            <label className="flex items-start gap-2 text-sm">
              <input type="checkbox" checked={saved} onChange={(event) => setSaved(event.target.checked)} />
              I have saved these codes somewhere private.
            </label>
            <button className="btn-primary w-full" disabled={!saved} type="button" onClick={() => router.replace("/home")}>
              Continue to attendance
            </button>
          </div>
        )}
        {error && <p className="mt-4 rounded-xl bg-red-50 px-3 py-2 text-sm text-red-800" role="alert">{error}</p>}
        {!status && <p className="mt-4 text-sm text-slate-600">Loading the next step…</p>}
      </div>
    </main>
  );
}
