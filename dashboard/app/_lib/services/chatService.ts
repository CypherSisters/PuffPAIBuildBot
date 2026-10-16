import { apiGet, apiPost, apiPostRaw, API_URL } from '../api';
import type { Conversation, ChatSource, MissingCardData, FixCardData } from '../../_interfaces/api';

export async function getChatHistory(): Promise<Conversation[]> {
  const res = await apiGet<{ success: boolean; conversations: Conversation[] }>('/chat/history');
  return res.conversations || [];
}

export interface ChatSendResult {
  response: string;
  conversation_id: string;
  timestamp: string;
  sources: ChatSource[];
  suggestions: string[];
  /** Set when the backend kept a personal detail out of the saved chat. */
  privacy?: ChatPrivacy;
  /** Said once per chat when older turns are kept as a short summary. */
  chatNotice?: string;
}

/** What the backend did with a personal detail it noticed in a message.
 *  `redactedMessage` is the copy that is safe to save and to show back;
 *  `notice` is the one plain sentence to show the person. */
export interface ChatPrivacy {
  notice: string;
  redactedMessage?: string;
}

function readPrivacy(pii: any): ChatPrivacy | undefined {
  if (!pii || typeof pii.notice !== 'string' || !pii.notice) return undefined;
  return {
    notice: pii.notice,
    redactedMessage:
      typeof pii.redacted_message === 'string' ? pii.redacted_message : undefined,
  };
}

export async function sendMessage(message: string, conversationId?: string): Promise<ChatSendResult> {
  const raw = await apiPost<any>('/chat/send', { message, conversation_id: conversationId });
  return {
    response: raw.response,
    conversation_id: raw.conversation_id,
    timestamp: raw.timestamp,
    sources: raw.sources || [],
    suggestions: raw.suggestions || [],
    privacy: readPrivacy(raw.pii),
    chatNotice: typeof raw.chat_notice === 'string' ? raw.chat_notice : undefined,
  };
}

/** Persist a user+assistant pair after streaming (so it shows in history).
 *  Pass the redacted copy the stream returned when there is one; the backend
 *  scans again either way and never writes a detail it finds. Returns the
 *  backend's notice if it kept something out on this call. */
export async function saveChat(
  conversationId: string,
  userMessage: string,
  assistantMessage: string
): Promise<ChatPrivacy | undefined> {
  const raw = await apiPost<any>('/chat/save', {
    conversation_id: conversationId,
    user_message: userMessage,
    assistant_message: assistantMessage,
  });
  return readPrivacy(raw?.pii);
}

export interface StreamMeta {
  sources: ChatSource[];
  suggestions: string[];
  privacy?: ChatPrivacy;
  /** Said once per chat when older turns are kept as a short summary. */
  chatNotice?: string;
}

/** A question the bot's tool loop asks before it runs something (a command,
 *  a remove). The loop waits until she answers with approveAsk. */
export interface ChatAsk {
  id: string;
  title: string;
  command?: string;
  folder?: string;
  detail?: string;
}

/** The tool loop's extra events: a plain line while a tool runs, and a
 *  question waiting for her Yes or No. */
export interface StreamSteps {
  onProgress?: (line: string) => void;
  onAsk?: (ask: ChatAsk) => void;
  onAnswered?: (id: string, yes: boolean) => void;
  /** A command started a screen on this computer (127.0.0.1 only). */
  onOpen?: (urls: string[]) => void;
  /** A command failed for want of a program, package or key. */
  onMissing?: (card: MissingCardData) => void;
  /** Something switched off or not saved yet: a "fix it now" card. */
  onFix?: (fix: FixCardData) => void;
  /** The whole answer so far, when the bot said a line twice and kept it once. */
  onReplace?: (text: string) => void;
  /** The backend's own words when it would not answer (a JSON error). */
  onProblem?: (message: string) => void;
}

/** What one tap on a "fix it now" card did. `again` means ask her last
 *  question again; `position` means a rough position is needed too. */
export interface FixResult {
  ok: boolean;
  line: string;
  again: boolean;
  position: boolean;
}

/** One tap on a "fix it now" card (POST /fix/apply). */
export async function applyFix(
  fix: FixCardData,
  value?: { town?: string; latitude?: string; longitude?: string },
): Promise<FixResult> {
  const body =
    fix.kind === 'switch_on'
      ? { kind: fix.kind, service: fix.service }
      : { kind: fix.kind, key: fix.key, value: value?.town || '', latitude: value?.latitude || '', longitude: value?.longitude || '' };
  try {
    const reply = await apiPostRaw<any>('/fix/apply', body);
    const d = reply.data || {};
    return {
      ok: !!d.ok,
      line: String(d.line || d.message || (reply.ok ? 'Done.' : 'That did not work.')),
      again: !!d.again,
      position: !!d.position,
    };
  } catch {
    return { ok: false, line: 'No answer from this computer.', again: false, position: false };
  }
}

/** The card an assembled app's own page asked for (POST /fix/card). The app
 *  id is the one this dashboard page shows, never the frame's; BuildBot gives
 *  the words, and only for a switch the app's own sources sit behind. */
export async function fixCardForApp(
  appId: string,
  ask: { kind: string; service?: string; name?: string },
): Promise<FixCardData | null> {
  try {
    const reply = await apiPostRaw<any>('/fix/card', {
      app_id: appId,
      kind: ask.kind,
      service: ask.service || '',
      name: ask.name || '',
    });
    const f = reply.ok && reply.data && reply.data.fix;
    if (!f || typeof f !== 'object') return null;
    const kind = String(f.kind || '');
    if (kind !== 'switch_on' && kind !== 'key') return null;
    return {
      kind,
      label: String(f.label || ''),
      service: String(f.service || ''),
      name: String(f.name || ''),
      detail: String(f.detail || ''),
      key: String(f.key || ''),
      input: String(f.input || ''),
      get_url: '',
      position: false,
    };
  } catch {
    return null;
  }
}

/** Her Yes or No to a question from the tool loop. */
export async function approveAsk(id: string, yes: boolean): Promise<boolean> {
  try {
    const raw = await apiPost<any>('/chat/approve', { id, yes });
    return !!raw?.success;
  } catch {
    return false;
  }
}

export function streamMessage(
  message: string,
  conversationId: string | undefined,
  onChunk: (text: string) => void,
  onDone: (meta?: StreamMeta) => void,
  steps?: StreamSteps
) {
  readStream(
    '/v1/chat/completions',
    {
      model: 'puffpai',
      messages: [{ role: 'user', content: message }],
      // The backend reads the earlier turns of this chat from the saved copy,
      // so the model sees the conversation, not just this one message.
      conversation_id: conversationId,
      stream: true,
    },
    onChunk,
    onDone,
    steps
  );
}

/** One earlier turn of a thing page's talk. */
export interface ThingTurn {
  user: string;
  assistant: string;
}

/** "Try it" and "Ask" on a thing's page: the same stream as the chat (its
 *  steps and Yes/No questions), with that thing loaded first. *history* is
 *  the page's last few turns, oldest first, so "Yes, run it." keeps its
 *  meaning. */
export function streamThingAsk(
  thingId: string,
  text: string,
  history: ThingTurn[],
  onChunk: (text: string) => void,
  onDone: (meta?: StreamMeta) => void,
  steps?: StreamSteps
) {
  readStream(`/things/${encodeURIComponent(thingId)}/ask`, { text, history }, onChunk, onDone, steps);
}

function readStream(
  path: string,
  body: unknown,
  onChunk: (text: string) => void,
  onDone: (meta?: StreamMeta) => void,
  steps?: StreamSteps
) {
  // No Authorization header: the /api/pai proxy attaches the backend key
  // server-side. The browser never holds it.
  fetch(`${API_URL}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  }).then(async (res) => {
    if (!res.ok) {
      const said = await res.json().catch(() => null);
      if (said && typeof said.message === 'string' && said.message) steps?.onProblem?.(said.message);
      throw new Error(res.statusText);
    }
    const reader = res.body?.getReader();
    const decoder = new TextDecoder();
    if (!reader) { onDone(); return; }

    let meta: StreamMeta | undefined;
    // An event can be split across two reads: keep the unfinished line.
    let pending = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const text = pending + decoder.decode(value, { stream: true });
      const lines = text.split('\n');
      pending = lines.pop() ?? '';
      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const data = line.slice(6);
          if (data === '[DONE]') { onDone(meta); return; }
          try {
            const parsed = JSON.parse(data);
            // Extract content chunks
            const content = parsed.choices?.[0]?.delta?.content || '';
            if (content) onChunk(content);
            // The tool loop's steps and questions
            if (parsed.puffpai_progress?.line) {
              steps?.onProgress?.(String(parsed.puffpai_progress.line));
            }
            if (parsed.puffpai_ask?.id) {
              const a = parsed.puffpai_ask;
              steps?.onAsk?.({
                id: String(a.id),
                title: String(a.title || 'May I go ahead?'),
                command: a.command ? String(a.command) : undefined,
                folder: a.folder ? String(a.folder) : undefined,
                detail: a.detail ? String(a.detail) : undefined,
              });
            }
            if (parsed.puffpai_answered?.id) {
              steps?.onAnswered?.(String(parsed.puffpai_answered.id), !!parsed.puffpai_answered.yes);
            }
            if (Array.isArray(parsed.puffpai_open?.urls)) {
              steps?.onOpen?.(parsed.puffpai_open.urls.map(String));
            }
            if (parsed.puffpai_missing && typeof parsed.puffpai_missing === 'object') {
              const m = parsed.puffpai_missing;
              const list = (v: unknown) => (Array.isArray(v) ? v.map(String).slice(0, 12) : []);
              steps?.onMissing?.({
                plan: String(m.plan || ''),
                line: String(m.line || ''),
                steps: list(m.steps),
                manual: list(m.manual),
                keys: Array.isArray(m.keys)
                  ? m.keys.slice(0, 12).map((k: any) => ({ name: String(k?.name || ''), get_url: String(k?.get_url || '') }))
                  : [],
                thing: String(m.thing || ''),
              });
            }
            if (parsed.puffpai_fix && typeof parsed.puffpai_fix === 'object') {
              const f = parsed.puffpai_fix;
              const kind = String(f.kind || '');
              if (kind === 'switch_on' || kind === 'setting' || kind === 'key' || kind === 'assemble_yes') {
                steps?.onFix?.({
                  kind,
                  label: String(f.label || ''),
                  service: String(f.service || ''),
                  name: String(f.name || ''),
                  detail: String(f.detail || ''),
                  key: String(f.key || ''),
                  input: String(f.input || ''),
                  get_url: String(f.get_url || ''),
                  position: !!f.position,
                });
              }
            }
            // Extract metadata from final chunk
            if (parsed.puffpai_meta) {
              if (typeof parsed.puffpai_meta.answer === 'string' && parsed.puffpai_meta.answer) {
                steps?.onReplace?.(parsed.puffpai_meta.answer);
              }
              meta = {
                sources: parsed.puffpai_meta.sources || [],
                suggestions: parsed.puffpai_meta.suggestions || [],
                privacy: readPrivacy(parsed.puffpai_meta.pii),
                chatNotice:
                  typeof parsed.puffpai_meta.chat_notice === 'string'
                    ? parsed.puffpai_meta.chat_notice
                    : undefined,
              };
            }
          } catch { /* skip non-JSON lines */ }
        }
      }
    }
    onDone(meta);
  }).catch(() => onDone());
}
