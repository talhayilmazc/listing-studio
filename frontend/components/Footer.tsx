"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

// Exact ToU notice — must not be altered or abbreviated.
const TRADEMARK_NOTICE =
  "The term 'Etsy' is a trademark of Etsy, Inc. This application uses the Etsy API but is not endorsed or certified by Etsy, Inc.";

export function Footer() {
  const [email, setEmail] = useState("support@example.com");

  useEffect(() => {
    api
      .meta()
      .then((m) => setEmail(m.support_email))
      .catch(() => {});
  }, []);

  return (
    <footer className="mt-16 border-t border-slate-200 bg-white">
      <div className="mx-auto max-w-6xl space-y-3 px-6 py-8 text-sm text-slate-500">
        <p className="max-w-3xl leading-relaxed">{TRADEMARK_NOTICE}</p>
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
          <Link href="/terms" className="hover:text-slate-800">
            Terms of Service
          </Link>
          <Link href="/privacy" className="hover:text-slate-800">
            Privacy Policy
          </Link>
          <span className="text-slate-300">·</span>
          <span>
            Support:{" "}
            <a href={`mailto:${email}`} className="font-medium text-brand-700 hover:underline">
              {email}
            </a>
          </span>
        </div>
      </div>
    </footer>
  );
}
