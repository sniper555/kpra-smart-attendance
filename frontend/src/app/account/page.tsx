"use client";

import { FormEvent, useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { api, ApiError, Me } from "@/lib/api";
import { creationOptions, credentialToJson } from "@/lib/webauthn";

function passkeyFailure(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof DOMException && err.name === "NotAllowedError") {
    return "The passkey prompt was closed. Click Add a passkey and finish Windows Hello, the phone prompt, or the security key.";
  }
  if (typeof window !== "undefined" && window.location.hostname !== "localhost") {
    return "Passkeys cannot be saved on this address. Open http://localhost:3000, sign in again, then click Add a passkey.";
  }
  return "The passkey was not saved. Click Add a passkey and finish the prompt.";
}

function mappingSentence(row: Me["user"]["timenex_mapping"]) {
  if (!row) return "TimeNex link: Not mapped. The employee ID does not sign you in.";
  const names: Record<string, string> = { VERIFIED: "Verified", PENDING: "Pending", REJECTED: "Rejected" };
  const name = names[row.verification_status] || row.verification_status;
  return `TimeNex link: ${name} · ${row.timenex_employee_id}. The employee ID does not sign you in.`;
}

export default function AccountPage() {
  return <Shell><AccountBody /></Shell>;
}

function AccountBody() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [passkeys, setPasskeys] = useState(0);
  const [mapping, setMapping] = useState("TimeNex link: Not mapped. The employee ID does not sign you in.");
  const [checkIn, setCheckIn] = useState("—");
  const [confirmed, setConfirmed] = useState(false);
  const [needsLocalhost, setNeedsLocalhost] = useState(false);

  useEffect(() => {
    const host = window.location.hostname;
    const numeric = host.includes(":") || /^\d{1,3}(\.\d{1,3}){3}$/.test(host);
    setNeedsLocalhost(numeric);
    api<Me>("/api/auth/me")
      .then((me) => {
        setPasskeys(me.user.passkey_count || 0);
        setMapping(mappingSentence(me.user.timenex_mapping));
        setCheckIn(me.user.remote_check_in || "—");
        setConfirmed(Boolean(me.user.timenex_confirmed));
      })
      .catch(() => undefined);
  }, []);

  async function addPasskey() {
    setError("");
    setMessage("");
    try {
      if (!window.PublicKeyCredential) {
        setError("This browser does not support passkeys.");
        return;
      }
      const options = await api<{ challenge_id: string; public_key: Record<string, unknown> }>(
        "/api/auth/webauthn/register/options",
        { method: "POST" },
      );
      const credential = (await navigator.credentials.create({
        publicKey: creationOptions(options.public_key),
      })) as PublicKeyCredential | null;
      if (!credential) {
        setError("No passkey was created.");
        return;
      }
      const saved = await api<{ passkey_count: number; message: string }>("/api/auth/webauthn/register/verify", {
        method: "POST",
        body: JSON.stringify({ challenge_id: options.challenge_id, credential: credentialToJson(credential) }),
      });
      setPasskeys(saved.passkey_count);
      setMessage(saved.message);
    } catch (err) {
      setError(passkeyFailure(err));
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setMessage("");
    try {
      await api("/api/auth/account/password", {
        method: "POST",
        body: JSON.stringify({ current_password: current, new_password: next }),
      });
      setCurrent("");
      setNext("");
      setMessage("Password changed. Other sessions were signed out.");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not change the password.");
    }
  }

  return (
    <section className="card max-w-lg space-y-3 p-5">
      <h1 className="text-2xl font-semibold">Account</h1>
      <p className="text-sm text-slate-600">
        Recovery codes were shown once during MFA enrollment. If you lose them, ask an administrator to reset MFA.
      </p>
      <div className="rounded-xl bg-slate-50 p-3 text-sm text-slate-700">
        <p>Browser check-in: {checkIn}. Confirmed by TimeNex: {confirmed ? "Yes" : "No"}.</p>
        <p className="mt-2">{mapping}</p>
        <p className="mt-2">Passkeys on this account: {passkeys}. A passkey stores a public key for this phone&apos;s fingerprint or face, Windows Hello, or a security key. It is not a fingerprint or a face template, and it does not prove where you are.</p>
        <p className="mt-2">On a phone, open the HTTPS name for this app and tap Add a passkey there. A passkey saved for localhost stays on this computer.</p>
        {needsLocalhost && <p className="mt-2">This address is an IP address, so the browser will not save a passkey. Use http://localhost:3000 on this computer, or an HTTPS name on the phone.</p>}
        <button className="btn-secondary mt-3" type="button" onClick={addPasskey}>Add a passkey</button>
      </div>
      <form className="space-y-3" onSubmit={submit}>
        <div>
          <label className="label" htmlFor="current">Current password</label>
          <input id="current" className="field" type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="next">New password</label>
          <input id="next" className="field" type="password" autoComplete="new-password" required value={next} onChange={(e) => setNext(e.target.value)} />
        </div>
        {error && <p className="text-sm text-red-800" role="alert">{error}</p>}
        {message && <p className="text-sm text-green-800">{message}</p>}
        <button className="btn-primary" type="submit">Change password</button>
      </form>
    </section>
  );
}
