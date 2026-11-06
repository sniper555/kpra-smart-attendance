"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, apiUrl, ApiError } from "@/lib/api";
import { credentialToJson, requestOptions } from "@/lib/webauthn";

type Health = { environment?: string; development_accounts?: string[] };

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [health, setHealth] = useState<Health>({});

  useEffect(() => {
    fetch(apiUrl("/api/health"))
      .then((response) => response.json())
      .then(setHealth)
      .catch(() => undefined);
  }, []);

  async function passkey() {
    setBusy(true);
    setError("");
    try {
      if (!window.PublicKeyCredential) {
        setError("This browser does not support passkeys. Use your email and password.");
        return;
      }
      const options = await api<{ challenge_id: string; public_key: Record<string, unknown> }>("/api/auth/webauthn/login/options", {
        method: "POST",
      });
      const credential = (await navigator.credentials.get({
        publicKey: requestOptions(options.public_key),
      })) as PublicKeyCredential | null;
      if (!credential) {
        setError("No passkey was selected.");
        return;
      }
      await api("/api/auth/webauthn/login/verify", {
        method: "POST",
        body: JSON.stringify({ challenge_id: options.challenge_id, credential: credentialToJson(credential) }),
      });
      router.push("/home");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "This device could not be verified.");
    } finally {
      setBusy(false);
    }
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/api/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
      router.push("/continue");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not sign in.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main id="main" className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-4 py-10">
      <div className="card p-6">
        <p className="text-xs font-semibold uppercase tracking-wider text-brand-600">KPRA</p>
        <h1 className="mt-1 text-2xl font-semibold text-navy-900">Smart Attendance Lite</h1>
        <p className="mt-2 text-sm text-slate-600">
          Sign in with your authorized manager account. A browser check-in is remote attendance, not a fingerprint or face scan.
        </p>
        <form className="mt-6 space-y-4" onSubmit={onSubmit}>
          <div>
            <label className="label" htmlFor="email">Email</label>
            <input id="email" className="field" type="email" autoComplete="username" required value={email} onChange={(event) => setEmail(event.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="password">Password</label>
            <input id="password" className="field" type="password" autoComplete="current-password" required value={password} onChange={(event) => setPassword(event.target.value)} />
          </div>
          {error && <p className="rounded-xl bg-red-50 px-3 py-2 text-sm text-red-800" role="alert">{error}</p>}
          <button className="btn-primary w-full" disabled={busy} type="submit">
            {busy ? "Signing in…" : "Continue"}
          </button>
        </form>
        <button className="btn-secondary mt-3 w-full" disabled={busy} type="button" onClick={passkey}>
          Sign in with a passkey
        </button>
        <p className="mt-2 text-xs text-slate-500">
          Windows Hello, Android biometrics, Face ID, Touch ID, a phone passkey, or a security key can be used when this browser offers them. The server stores a public key only. It cannot tell a fingerprint from a device PIN, and a passkey does not prove where you are.
        </p>
        {health.development_accounts && health.development_accounts.length > 0 && (
          <p className="mt-4 text-xs text-slate-500">
            Development accounts: {health.development_accounts.join(", ")}. Passwords are in the local .env file. MFA enrollment happens after sign-in.
          </p>
        )}
      </div>
    </main>
  );
}
