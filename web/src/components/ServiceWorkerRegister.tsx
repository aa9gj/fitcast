"use client";

import { useEffect } from "react";

// Registers the hand-rolled app-shell service worker (public/sw.js) so the
// site is installable and the showcase works offline. The demo itself needs
// the network (job board + Anthropic) and says so when offline.
export default function ServiceWorkerRegister() {
  useEffect(() => {
    if (process.env.NODE_ENV !== "production") return;
    if (!("serviceWorker" in navigator)) return;
    const onLoad = () => {
      navigator.serviceWorker.register("/sw.js").catch(() => {
        /* non-fatal: the app works fine without the SW */
      });
    };
    window.addEventListener("load", onLoad);
    return () => window.removeEventListener("load", onLoad);
  }, []);
  return null;
}
