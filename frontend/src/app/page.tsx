"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, Me } from "@/lib/api";

export default function IndexPage() {
  const router = useRouter();
  const [message, setMessage] = useState("Checking your session…");

  useEffect(() => {
    api<Me>("/api/auth/me")
      .then(() => router.replace("/home"))
      .catch(() => router.replace("/login"));
  }, [router]);

  return (
    <main className="mx-auto max-w-lg px-4 py-16 text-center text-navy-800">{message}</main>
  );
}
