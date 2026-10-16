export interface SystemStatus {
  knowledge: { status: string; library_count: number; chunk_count: number };
  voice: { status: string; wake_word: string };
  firewall: { status: string; blocked_count: number; rules_count: number };
  vault: { status: string; item_count: number };
  inference: { status: string; model: string; backend: string };
  uptime_seconds: number;
}

export interface ChatSource {
  library: string;
  collection: string;
  type: string;       // 'video' | 'encyclopedia' | 'qa' | 'medical' | 'survival' | 'reference' | etc.
  title: string;
  url: string;
  relevance: number;  // 0–1
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
  sources?: ChatSource[];
  suggestions?: string[];
  /** One plain sentence from the backend when it kept a personal detail out
   *  of the saved chat (e.g. "I kept the phone number out of my saved chats
   *  and put it in your locked vault."). Shown under the reply. */
  privacyNotice?: string;
  /** Said once per chat when older turns are kept as a short summary. */
  chatNotice?: string;
  /** Plain lines from the tool loop while it worked ("Looking on GitHub ..."). */
  progress?: string[];
  /** Local screens (127.0.0.1) a command started: shown as Open buttons. */
  opens?: string[];
  /** A command failed for want of a program, package or key: the
   *  "Install what's missing" card (src/puffpai/deps.py). */
  missing?: MissingCardData;
  /** "Fix it now" cards: something switched off or not saved yet, fixed
   *  with one tap (src/puffpai/fix_api.py, POST /fix/apply). */
  fixes?: FixCardData[];
  /** A question the tool loop asked before running something; `answered` is
   *  her Yes (true) or No (false) once given. */
  ask?: {
    id: string;
    title: string;
    command?: string;
    folder?: string;
    detail?: string;
    answered?: boolean;
  };
}

export interface Conversation {
  id: string;
  messages: ChatMessage[];
  created_at: string;
  title?: string;
}

/** A zone identifier. Legacy ChromaDB rows use the numbers 1 (vault) and 2
 *  (general); ZIM-backed rows report the string "knowledge". Treat anything
 *  else as an unlabelled general zone rather than crashing. */
export type KnowledgeZone = 1 | 2 | string | number;

export interface KnowledgeLibrary {
  name: string;
  collection: string;
  zone: KnowledgeZone;
  zone_label?: string;
  /** Absent on ZIM rows that were never ingested into ChromaDB. */
  chunk_count?: number;
  /** Bytes (number) on ZIM rows, pre-formatted string on legacy rows. */
  size?: string | number;
  added?: string;
  library_id?: string;
  content_type?: 'encyclopedia' | 'video' | 'qa' | 'medical' | 'reference' | 'survival' | 'science' | 'travel' | 'books' | 'uploads';
  // User-upload / source-material metadata (populated by /knowledge/libraries).
  uploaded_at?: string;
  is_upload?: boolean;
  source_available?: boolean;
  source_content_type?: string;
  /** "zim" for packs discovered on disk, "chromadb" for collections/uploads. */
  source?: 'zim' | 'chromadb' | string;
  /** True when a SQLite FTS5 shard exists, i.e. full-text search works. */
  fts_ready?: boolean;
  zim_filename?: string;
}

export interface LibraryArticle {
  index: number;
  title: string;
  url: string;
  mime_type: string;
}

export interface ArticleContent {
  title: string;
  url: string;
  mime_type: string;
  html: string;
}

export interface VaultItem {
  key: string;
  // Vaulted raw documents have no explicit type; auto-detected PII log
  // and manually-stored items will set this.
  type?: string;
  // API returns a byte count (number), not a pre-formatted string.
  size: number;
  // API uses 'modified' (ISO-8601). Older copies may still send
  // 'last_modified' — keep both optional to avoid breakage.
  modified?: string;
  last_modified?: string;
  // Extras returned for auto_detected_pii.json
  record_count?: number;
  pii_types?: string[];
}

export interface FirewallRule {
  id: string;
  name: string;
  direction: 'inbound' | 'outbound' | 'both';
  action: 'allow' | 'block';
  protocol: string;
  port: string;
  status: 'active' | 'inactive';
}

export interface NetworkDevice {
  name: string;
  ip: string;
  mac: string;
  device_class: string;
  trust: 'trusted' | 'classified' | 'unknown' | 'blocked';
  blocked?: boolean;
  zone3_managed?: boolean;
  zone3_mode?: 'lockdown' | 'balanced' | 'open' | 'always_allow' | null;
}

export interface UserProfile {
  name: string;
  email: string;
  avatar?: string;
}

export interface AISettings {
  ai_name: string;
  avatar_style: string;
  /**
   * BROWSER-SIDE ONLY, both of these. PUT /profile/ai accepts ai_name,
   * avatar_style, avatar_seed, personality, temperature, context_length,
   * system_prompt and response_style — and 400s naming anything else, so
   * putting either field in a save payload breaks the whole save. They are
   * typed here because the browser carries them (localStorage, and
   * /avatar/custom for the file); they are not settings on that endpoint.
   */
  /** Which avatar engine to render: 'puff' (SVG), 'human' (TalkingHead 3D), or 'glb' (custom model) */
  avatar_character?: string;
  /** Model URL for the 3D engines. On this build it is always the
   *  /avatar/custom slot — see app/_lib/services/avatarService.ts. */
  avatar_3d_url?: string;
  personality: string;
  /** The owner's own standing instructions, shown as "Custom instructions".
   *  Appended to what the bot is told before every message, so a change applies
   *  from the next one on. Optional because an older ai_settings.json has never
   *  had one. */
  system_prompt?: string;
  temperature: number;
  context_length: number;
  model: string;
  backend: string;
  embedding_model: string;
}

export interface AppData {
  items: any[];
  last_updated: string;
}

/** "Install what's missing": what the backend says is missing and the plan
 *  one yes installs (POST /things/install-missing with `plan`). Words only. */
export interface MissingCardData {
  plan: string;
  line: string;
  steps: string[];
  manual: string[];
  keys: { name: string; get_url: string }[];
  thing: string;
}

/** "Fix it now": one card in the chat for something switched off
 *  (`switch_on`), a setting not saved yet (`setting`, today the home town) or
 *  a sign-up key that is missing (`key`). Words and ids only. */
export interface FixCardData {
  kind: 'switch_on' | 'setting' | 'key' | 'assemble_yes';
  label: string;
  /** The outside service to switch on (`switch_on`). */
  service: string;
  name: string;
  /** The server's own sentence: what leaves and where it goes, or what is kept. */
  detail: string;
  /** The setting (`setting`): "home". */
  key: string;
  /** "place" for a town. */
  input: string;
  /** Where to get a key (`key`). */
  get_url: string;
  /** This bot keeps everything on this computer: ask for a rough position too. */
  position: boolean;
}
