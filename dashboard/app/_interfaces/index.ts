export type UserPayloadObject = {
  name: string;
  email: string;
};

export type MenuAsideItem = {
  label: string;
  icon?: string;
  href?: string;
  target?: string;
  color?: ColorButtonKey;
  isLogout?: boolean;
  menu?: MenuAsideItem[];
  /** Material Symbols Rounded ligature name (rendered via MsIcon in the rail). */
  msIcon?: string;
  /**
   * Where the item renders in the sidebar rail. Omitted → the item is NOT
   * shown in the rail but stays in menuAside so QuickSearch keeps indexing
   * it (absorbed routes: documents, firewall, workflows, profile, apps).
   */
  railGroup?: "primary" | "secondary" | "settings";
};

export type MenuNavBarItem = {
  label?: string;
  icon?: string;
  href?: string;
  target?: string;
  isDivider?: boolean;
  isLogout?: boolean;
  isDesktopNoLabel?: boolean;
  isToggleLightDark?: boolean;
  isCurrentUser?: boolean;
  menu?: MenuNavBarItem[];
  /** Material Symbols Rounded ligature name (rendered via MsIcon). */
  msIcon?: string;
};

export type ColorKey = "white" | "light" | "contrast" | "success" | "danger" | "warning" | "info";

export type ColorButtonKey =
  | "white"
  | "whiteDark"
  | "lightDark"
  | "contrast"
  | "success"
  | "danger"
  | "warning"
  | "info"
  | "void";

export type BgKey = "purplePink" | "pinkRed";

export type TrendType = "up" | "down" | "success" | "danger" | "warning" | "info";

export type TransactionType = "withdraw" | "deposit" | "invoice" | "payment";

export type Transaction = {
  id: number;
  amount: number;
  account: string;
  name: string;
  date: string;
  type: TransactionType;
  business: string;
};

export type Client = {
  id: number;
  login: string;
  name: string;
  company: string;
  city: string;
  progress: number;
  created: string;
  created_mm_dd_yyyy: string;
};

export type UserForm = {
  name: string | null;
  email: string | null;
};
