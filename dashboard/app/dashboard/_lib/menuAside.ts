import {
  mdiMonitor,
  mdiMicrophone,
  mdiMessageText,
  mdiBookOpenPageVariant,
  mdiShieldLock,
  mdiHome,
  mdiAccountCircle,
  mdiCog,
  mdiAlarmLight,
  mdiCalendar,
  mdiFood,
  mdiFire,
  mdiHelpCircle,
  mdiAccountVoice,
  mdiAccountGroup,
  mdiFileDocumentMultipleOutline,
  mdiChip,
  mdiHeartPulse,
  mdiRocketLaunchOutline,
  mdiViewGridOutline,
  mdiEarth,
  mdiKeyVariant,
  mdiPalette,
  mdiPlusCircleOutline,
  mdiCodeBraces,
} from "@mdi/js";
import { MenuAsideItem } from "../../_interfaces";

/**
 * Sidebar + search destinations.
 *
 * railGroup decides where an entry renders in the rail:
 *   "primary"   → Dashboard / Talk / Chat / Avatar / Device
 *   "secondary" → dimmed group (Knowledge / Vault / Smart Home / Agent Team)
 *   "settings"  → the Settings button above the security-zone card
 *   omitted     → NOT in the rail, but kept here so QuickSearch still indexes
 *                 the absorbed routes (documents, profile, apps). The mdi `icon` is what QuickSearch's dropdown renders;
 *                 `msIcon` is the Material Symbols ligature the rail renders.
 */
const menuAside: MenuAsideItem[] = [
  /* ── Primary nav ── */
  {
    href: "/dashboard",
    label: "Dashboard",
    icon: mdiMonitor,
    msIcon: "space_dashboard",
    railGroup: "primary",
  },
  {
    href: "/dashboard/talk",
    label: "Talk",
    icon: mdiMicrophone,
    msIcon: "graphic_eq",
    railGroup: "primary",
  },
  {
    href: "/dashboard/chat",
    label: "Chat",
    icon: mdiMessageText,
    msIcon: "chat_bubble",
    railGroup: "primary",
  },
  {
    href: "/dashboard/avatar",
    label: "Avatar",
    icon: mdiAccountVoice,
    msIcon: "face_retouching_natural",
    railGroup: "primary",
  },
  {
    href: "/dashboard/device",
    label: "Device",
    icon: mdiChip,
    msIcon: "memory",
    railGroup: "primary",
  },

  /* ── Secondary (dimmed) group ── */
  {
    href: "/dashboard/knowledge",
    label: "Knowledge",
    icon: mdiBookOpenPageVariant,
    msIcon: "menu_book",
    railGroup: "secondary",
  },
  {
    // Find more after the build: skills, tools and apps others built, books,
    // live cards. The page's own title says "Add to <bot>". Every build has
    // it (no surface mapping).
    href: "/dashboard/add",
    label: "Find more",
    icon: mdiPlusCircleOutline,
    msIcon: "add_circle",
    railGroup: "secondary",
  },
  {
    href: "/dashboard/vault",
    label: "Vault",
    icon: mdiShieldLock,
    msIcon: "lock",
    railGroup: "secondary",
  },
  {
    href: "/dashboard/homeassistant",
    label: "Smart Home",
    icon: mdiHome,
    msIcon: "home",
    railGroup: "secondary",
  },
  {
    href: "/dashboard/agents",
    label: "Agent Team",
    icon: mdiAccountGroup,
    msIcon: "groups",
    railGroup: "secondary",
  },
  {
    // Every outside service, its switch, and every request BuildBot made.
    // No surface mapping on purpose: every build has this page.
    href: "/dashboard/outside",
    label: "What leaves your computer",
    icon: mdiEarth,
    msIcon: "public",
    railGroup: "secondary",
  },
  {
    // Free sign-up keys: the steps to get one, a hidden box to paste it into
    // (it goes straight into the vault), and each key's state. No surface
    // mapping on purpose: every build can add a key.
    href: "/dashboard/keys",
    label: "Sign-up keys",
    icon: mdiKeyVariant,
    msIcon: "key",
    railGroup: "secondary",
  },

  /* ── Setup + Settings (bottom, above the security-zone card) ──
        Setup re-opens the onboarding wizard. README §"First run" and
        docs/GETTING_STARTED both tell the owner to re-run onboarding "from the
        sidebar's Setup entry" — there was no such entry, so the instruction
        sent people looking for a control that did not exist. It carries no
        surface mapping in SURFACES_BY_HREF, which is deliberate: every build
        can be set up again, whatever it was built for. ── */
  {
    href: "/onboarding",
    label: "Setup",
    icon: mdiRocketLaunchOutline,
    msIcon: "rocket_launch",
    railGroup: "settings",
  },
  {
    // How the dashboard looks, from the owner's own words. Every build has it.
    href: "/dashboard/looks",
    label: "Looks",
    icon: mdiPalette,
    msIcon: "palette",
    railGroup: "settings",
  },
  {
    // The Developer area: the vault, the brain and the Bridge, each with the
    // terminal commands for it. Always shown;
    // changes still need a paired device, and vault changes the PIN. No
    // surface mapping on purpose: every build has it.
    href: "/dashboard/developer",
    label: "Developer",
    icon: mdiCodeBraces,
    msIcon: "code",
    railGroup: "settings",
  },
  {
    href: "/dashboard/settings",
    label: "Settings",
    icon: mdiCog,
    msIcon: "settings",
    railGroup: "settings",
  },

  /* ── Absorbed routes: search-only (no railGroup → hidden from the rail).
        Knowledge absorbs My Documents, the topbar chip absorbs Profile, and
        the dashboard Apps tile absorbs the Apps submenu. Routes stay alive. ── */
  {
    href: "/dashboard/documents",
    label: "My Documents",
    icon: mdiFileDocumentMultipleOutline,
  },
  {
    href: "/dashboard/profile",
    label: "Profile",
    icon: mdiAccountCircle,
  },
  {
    // The submenu keeps its own href so /dashboard/apps — the index — is a real
    // destination, findable by typing "apps". filterMenuByBuild keeps a submenu
    // that has an href of its own even when every child drops out, and that is
    // the right answer here: the index says plainly that this build carries no
    // built-in apps, which beats a dead link.
    label: "Apps",
    href: "/dashboard/apps",
    icon: mdiViewGridOutline,
    menu: [
      {
        label: "AlarmFlow",
        icon: mdiAlarmLight,
        href: "/dashboard/apps/alarmflow",
      },
      {
        label: "Calendar",
        icon: mdiCalendar,
        href: "/dashboard/apps/calendar",
      },
      {
        label: "FreshTable",
        icon: mdiFood,
        href: "/dashboard/apps/freshtable",
      },
      {
        label: "FitFam",
        icon: mdiFire,
        href: "/dashboard/apps/fitfam",
      },
      {
        label: "Trivia",
        icon: mdiHelpCircle,
        href: "/dashboard/apps/trivia",
      },
    ],
  },
];

export default menuAside;
