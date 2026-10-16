import { mdiAccount, mdiLogout } from "@mdi/js";
import { MenuNavBarItem } from "../../_interfaces";

/**
 * Operator-chip dropdown in the design topbar (spec §3b item 8).
 *
 * The dashboard has no real session to destroy — the template's old Logout
 * item never had a handler. "Log Out" now routes to /login so the action
 * stays reachable and meaningful once auth exists.
 */
const menuNavBar: MenuNavBarItem[] = [
  {
    icon: mdiAccount,
    msIcon: "person",
    label: "My Profile",
    href: "/dashboard/profile",
  },
  {
    icon: mdiLogout,
    msIcon: "logout",
    label: "Log Out",
    isLogout: true,
  },
];

export default menuNavBar;
