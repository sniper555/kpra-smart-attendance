"use client";

import { useEffect, useState } from "react";

type BeforeInstallPromptEvent = Event & {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
};

function standalone() {
  const nav = navigator as Navigator & { standalone?: boolean };
  return window.matchMedia("(display-mode: standalone)").matches || nav.standalone === true;
}

export function InstallBanner() {
  const [install, setInstall] = useState<BeforeInstallPromptEvent | null>(null);
  const [showIos, setShowIos] = useState(false);
  const [hidden, setHidden] = useState(false);

  useEffect(() => {
    if (standalone()) return;
    const onPrompt = (event: Event) => {
      event.preventDefault();
      setInstall(event as BeforeInstallPromptEvent);
    };
    window.addEventListener("beforeinstallprompt", onPrompt);
    const ua = navigator.userAgent;
    if (/iphone|ipad|ipod/i.test(ua)) setShowIos(true);
    return () => window.removeEventListener("beforeinstallprompt", onPrompt);
  }, []);

  if (hidden || (!install && !showIos)) return null;

  return (
    <div className="fixed inset-x-3 top-3 z-40 rounded-2xl border border-slate-200 bg-white p-3 shadow-lg">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-[#071a33]">Install app</p>
          <p className="mt-1 text-sm text-slate-600">
            {install
              ? "Add KPRA Attendance to your home screen. It opens without the browser bar."
              : "In Safari, tap Share, then Add to Home Screen."}
          </p>
        </div>
        <button className="min-h-10 shrink-0 px-2 text-sm font-semibold text-slate-500" type="button" onClick={() => setHidden(true)}>
          Not now
        </button>
      </div>
      {install && (
        <button className="btn-primary mt-3 w-full" type="button" onClick={() => install.prompt()}>
          Add to Home Screen
        </button>
      )}
    </div>
  );
}
