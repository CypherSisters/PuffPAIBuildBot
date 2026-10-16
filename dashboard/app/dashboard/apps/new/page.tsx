import React from "react";
import type { Metadata } from "next";
import { getPageTitle } from "../../../_lib/config";
import PersonaBuilder from "../_components/PersonaBuilder";

export const metadata: Metadata = {
  title: getPageTitle("Build a persona"),
};

// A STATIC segment under apps/, so Next resolves it before the dynamic `[id]`
// route and "new" is the builder rather than a lookup for an app called "new".
// The body is a client component because it previews as you type; the title
// stays on the server, like every other page here.
export default function NewPersonaPage() {
  return <PersonaBuilder />;
}
