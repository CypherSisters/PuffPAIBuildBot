import React from "react";
import type { Metadata } from "next";
import { getPageTitle } from "../../_lib/config";
import AppsIndex from "./_components/AppsIndex";

export const metadata: Metadata = {
  title: getPageTitle("Apps"),
};

// The index the sidebar's Apps submenu never had. It reads that same submenu,
// through the same surface gate, so this page and the nav cannot disagree about
// what this build carries. The body is a client component because the gate
// needs GET /build/self; the title stays here on the server.
export default function AppsIndexPage() {
  return <AppsIndex />;
}
