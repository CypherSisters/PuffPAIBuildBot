"use client";

/**
 * Developer: the overview. At the top, "Add your own" (AddYourOwn: a folder
 * of her code, a program, a desktop app, a tool server). Then three cards, one for each part of the bot, read
 * from /build/self (the build's own description of itself). Each card has a
 * status line and opens that part's page. Below them: "Add a tool" (the three
 * plain ways to add one) and the command line tile (CliTile: grows in place,
 * or opens in its own window).
 *
 * The part names carry their numbers here, and only in this folder;
 * the everyday pages keep the plain names.
 */

import Link from "next/link";
import { useEffect, useState } from "react";
import MsIcon from "@/app/_components/MsIcon";
import AddATool from "@/app/_components/AddATool";
import AddYourOwn from "./_components/AddYourOwn";
import CliTile from "./_components/CliTile";
import GitHubTile from "./_components/GitHubTile";
import { getBuildSelf, type BuildSelf } from "@/app/_lib/services/buildService";
import { AppHeader, AppPage, FREDOKA, NUNITO, microLabel, panelStyle } from "@/app/dashboard/apps/_components/ui";
import { getPrivacy, internetWords, type PrivacyState } from "./developerService";
import { vaultStatus } from "./overviewWords";
import { consentLine, countWords, onCount } from "../_lib/consentWords";

type Card = {
  href: string;
  zone: string;
  name: string;
  icon: string;
  color: string;
  status: string;
  more: string;
};

function cardsFor(build: BuildSelf | null, privacy: PrivacyState | null): Card[] {
  const vault = build?.zones.vault;
  const brain = build?.zones.brain;
  const bridge = build?.zones.bridge;
  // The count is the one "What leaves your computer", the sidebar card and
  // the home page show (/system/privacy), not the build's own list, which
  // can say 0 while 12 are on.
  const internet = internetWords(bridge?.run_egress);
  const services = onCount(privacy) === null ? "" : `, ${countWords(privacy).toLowerCase()}`;
  const model = brain?.model ?? "";
  const answers = brain?.backend_status === "connected" ? "answering" : "not answering right now";
  return [
    {
      href: "/dashboard/developer/vault",
      zone: "Zone 1",
      name: "Vault",
      icon: "lock",
      color: "var(--magenta)",
      status: vaultStatus(vault),
      more: "Where your private things are kept, and how they are locked.",
    },
    {
      href: "/dashboard/developer/brain",
      zone: "Zone 2",
      name: "Brain",
      icon: "neurology",
      color: "var(--sky)",
      status: !brain
        ? "Could not be read"
        : model
          ? `${model}, ${answers}`
          : "No model chosen yet",
      more: "The model your bot thinks with, where it runs, and Home Assistant.",
    },
    {
      href: "/dashboard/developer/bridge",
      zone: "Zone 3",
      name: "Bridge",
      icon: "public",
      color: "var(--lemon)",
      status: !bridge
        ? "Could not be read"
        : `${internet}${services}`,
      more: "What may leave your computer, your paired devices, and your own gear.",
    },
  ];
}

export default function DeveloperPage() {
  const [build, setBuild] = useState<BuildSelf | null>(null);
  const [privacy, setPrivacy] = useState<PrivacyState | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let alive = true;
    Promise.all([getBuildSelf(), getPrivacy()]).then(([b, p]) => {
      if (!alive) return;
      setBuild(b);
      setPrivacy(p);
      setLoaded(true);
    });
    return () => {
      alive = false;
    };
  }, []);

  return (
    <AppPage>
      <AppHeader
        title="Developer"
        lede="Add what you already have: your code, your programs, your apps. Below: the three parts of your bot, what each does today, and the commands for each."
      />
      {loaded && !build && (
        <p style={{ fontFamily: NUNITO, color: "var(--text2)", margin: 0 }}>
          Your bot did not answer, so its parts cannot be shown right now.
        </p>
      )}
      {loaded && consentLine(privacy) && (
        <p data-consent-line style={{ fontFamily: NUNITO, fontWeight: 700, color: "var(--text)", margin: 0 }}>
          {consentLine(privacy)}
        </p>
      )}
      <AddYourOwn />
      <div className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))" }}>
        {cardsFor(loaded ? build : null, loaded ? privacy : null).map((c) => (
          <Link
            key={c.href}
            href={c.href}
            data-dev-card={c.name}
            className="transition-transform hover:-translate-y-0.5"
            style={{ ...panelStyle, color: "var(--text)", textDecoration: "none", display: "grid", gap: 10 }}
          >
            <div className="flex items-center gap-3">
              <MsIcon name={c.icon} size={26} style={{ color: c.color }} />
              <div style={microLabel}>{c.zone}</div>
            </div>
            <div style={{ fontFamily: FREDOKA, fontWeight: 600, fontSize: 28, lineHeight: 1 }}>{c.name}</div>
            <div style={{ fontFamily: NUNITO, fontWeight: 700, fontSize: 15, color: c.color }}>
              {loaded ? c.status : "Checking"}
            </div>
            <p style={{ margin: 0, fontFamily: NUNITO, fontSize: 14, color: "var(--text2)", lineHeight: 1.45 }}>
              {c.more}
            </p>
            <div className="flex items-center gap-1" style={{ fontFamily: NUNITO, fontWeight: 700, fontSize: 14 }}>
              Open <MsIcon name="arrow_forward" size={18} />
            </div>
          </Link>
        ))}
      </div>
      <AddATool where="developer" />
      <div className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(360px, 1fr))" }}>
        <CliTile />
        <GitHubTile />
      </div>
    </AppPage>
  );
}
