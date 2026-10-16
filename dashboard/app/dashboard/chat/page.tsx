"use client";

/**
 * Chat — design-match restyle (03 Chat).
 *
 * Two-column layout: glass conversation card + right rail ("What Puff used"
 * with live zone counts, "Recent" with real conversations). All previous
 * functionality is kept: streaming send with non-streaming fallback, history
 * load, voice input (browser STT), spoken replies, file attach/ingest,
 * copy/speak/retry, sources and follow-up suggestions.
 *
 * Copyright (c) 2026 Cypher Sisters. Apache-2.0 License.
 */

import React, { useState, useRef, useEffect, useCallback } from "react";
import dynamic from "next/dynamic";
import MsIcon from "../../_components/MsIcon";
import { useAppDispatch, useAppSelector } from "@/app/_stores/hooks";
import {
  fetchChatHistory,
  setActiveConversation,
  clearMessages,
} from "@/app/_stores/chatSlice";
import { fetchSystemStatus } from "@/app/_stores/systemSlice";
import { streamMessage, sendMessage, saveChat, approveAsk } from "@/app/_lib/services/chatService";
import type { StreamMeta } from "@/app/_lib/services/chatService";
import ToolSteps from "./ToolSteps";
import {
  speak,
  listen,
  browserSpeakStop,
  isBrowserSttSupported,
  getVoiceCapabilities,
} from "@/app/_lib/services/voiceService";
import { setAvatarSpeaking, setIsListening } from "@/app/_stores/avatarSlice";
import { apiGet, checkHealth } from "@/app/_lib/api";
import { conversationTitle } from "@/app/_lib/chatTitle";
import { getLibraries } from "@/app/_lib/services/knowledgeService";
import { useBuildSelf } from "@/app/_lib/hooks/useBuildSelf";
import { assistantName } from "@/app/_lib/surfaceMap";
import type { ChatMessage, KnowledgeLibrary } from "@/app/_interfaces/api";

const AvatarOverlay = dynamic(
  () => import("../../_components/Avatar/AvatarOverlay"),
  { ssr: false }
);

/* ── Design-language shorthands ────────────────────────────── */

/**
 * Conversation id, without assuming a secure context.
 *
 * crypto.randomUUID() only exists on HTTPS or localhost. The kiosk reaches the
 * dashboard as http://localhost:3003 so it is fine there — but any other
 * household device browses to http://192.168.50.10:3003, where randomUUID is
 * undefined and starting a NEW conversation threw before the message was ever
 * sent. getRandomValues is available on plain http, so use it directly.
 */
function newConversationId(): string {
  const bytes = new Uint8Array(16);
  if (typeof crypto !== "undefined" && crypto.getRandomValues) {
    crypto.getRandomValues(bytes);
  } else {
    for (let i = 0; i < bytes.length; i++) {
      bytes[i] = Math.floor(Math.random() * 256);
    }
  }
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

const FREDOKA = "var(--font-fredoka), sans-serif";
const NUNITO = "var(--font-nunito), sans-serif";
const JBM = "var(--font-jbmono), monospace";

const STRIPES =
  "repeating-linear-gradient(135deg, var(--chip) 0 8px, transparent 8px 16px)";

export default function ChatPage() {
  const dispatch = useAppDispatch();
  const { conversations, activeConversationId } =
    useAppSelector((state) => state.chat);
  const systemStatus = useAppSelector((state) => state.system.status);

  const [input, setInput] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploadingFile, setUploadingFile] = useState(false);
  // True while the current turn was initiated by voice → speak the reply
  const voiceTurnRef = useRef(false);

  // Right-rail data: real library counts (uploads vs knowledge packs).
  // null = not loaded yet / unavailable → striped placeholder, never fake.
  const [libraries, setLibraries] = useState<KnowledgeLibrary[] | null>(null);
  // Who this bot is: the name the header shows and the greeting, both from
  // /build/self (bot_identity on the backend). The page used to open with a
  // hard-coded product greeting whatever the owner had named the bot.
  const { build } = useBuildSelf();
  const aiName = assistantName(build);
  const greeting =
    build?.build?.greeting?.trim() ||
    `Hello! I'm ${aiName}. Nothing leaves your computer without your consent. How can I help?`;

  useEffect(() => {
    getLibraries()
      .then((libs) => setLibraries(libs))
      .catch(() => setLibraries(null));
  }, []);

  // Attach → ingestion pipeline: extract, classify, PII-scan (auto-vault),
  // chunk + index into the doc store the agent searches (search_my_documents).
  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploadingFile(true);
    // Same-origin proxy path only, never an env-supplied base: such a
    // base can point the browser past the /api/pai proxy and so past
    // the pairing gate, the path allowlist and the cross-site check.
    const apiBase = "/api/pai";
    const say = (content: string) =>
      setLocalMessages((prev) => [
        ...prev,
        { id: `msg-${Date.now()}`, role: "assistant" as const, content, timestamp: new Date().toISOString() },
      ]);
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("auto_apply", "true");
      const res = await fetch(`${apiBase}/ingest/upload`, { method: "POST", body: fd });
      const j = await res.json().catch(() => null);
      if (res.ok && j?.success !== false) {
        say(
          `📄 "${file.name}" uploaded and indexed${
            j?.pii_vaulted
              ? ". It had personal details in it: the original is locked in your vault, and the copy I can search has them taken out"
              : ""
          }. Ask me anything about it.`
        );
      } else {
        say(`I couldn't index "${file.name}": ${j?.error ?? `HTTP ${res.status}`}`);
      }
    } catch {
      say(`Upload failed. Is the backend running?`);
    } finally {
      setUploadingFile(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const WELCOME: ChatMessage = {
    id: "msg-welcome",
    role: "assistant",
    content: greeting,
    timestamp: new Date().toISOString(),
  };
  const [localMessages, setLocalMessages] = useState<ChatMessage[]>([WELCOME]);

  // The greeting arrives with /build/self, after the first paint: swap it into
  // the welcome line (and only that line) when it does.
  useEffect(() => {
    setLocalMessages((prev) =>
      prev.map((m) =>
        m.id === "msg-welcome" && m.content !== greeting
          ? { ...m, content: greeting }
          : m
      )
    );
  }, [greeting]);
  const [localTyping, setLocalTyping] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [connectionOk, setConnectionOk] = useState<boolean | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [failedMessageId, setFailedMessageId] = useState<string | null>(null);
  const [isRecording, setIsRecording] = useState(false);
  const [voiceEnabled, setVoiceEnabled] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const listenRef = useRef<{ stop: () => void } | null>(null);

  // Scroll to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [localMessages, localTyping]);

  // Check backend health on mount
  useEffect(() => {
    checkHealth().then((ok) => setConnectionOk(ok));
    dispatch(fetchSystemStatus());
    dispatch(fetchChatHistory());
  }, [dispatch]);

  // Keep the caret in the composer. Anything that steals focus mid-conversation
  // (the send/mic buttons, a suggestion chip, a re-render while streaming) hands
  // it back the moment Puff finishes, so the next message is always type-ready.
  // Skipped when focus is somewhere deliberate — another input, or a button the
  // user is actually operating.
  // (effect lives below, next to disableInput's declaration)

  // Periodically check health
  useEffect(() => {
    const interval = setInterval(async () => {
      const ok = await checkHealth();
      setConnectionOk(ok);
    }, 15_000);
    return () => clearInterval(interval);
  }, []);

  // Detect voice capabilities on mount
  useEffect(() => {
    const checkVoice = async () => {
      const caps = await getVoiceCapabilities();
      const browserStt = isBrowserSttSupported();
      setVoiceEnabled(caps.stt_available || browserStt);
    };
    checkVoice();
  }, []);

  // Auto-resize textarea
  useEffect(() => {
    const ta = textareaRef.current;
    if (ta) {
      ta.style.height = "auto";
      ta.style.height = `${Math.min(ta.scrollHeight, 120)}px`;
    }
  }, [input]);

  const handleSend = useCallback(
    async (retryContent?: string) => {
      const text = (retryContent || input).trim();
      if (!text || localTyping || isStreaming) return;

      // Clicking the send button (or the mic) moves focus off the composer;
      // put it straight back so the next question can just be typed.
      textareaRef.current?.focus();

      setFailedMessageId(null);

      // Ensure we have a conversation ID for persistence
      const convId = activeConversationId ?? newConversationId();
      if (!activeConversationId) {
        dispatch(setActiveConversation(convId));
      }

      // Add user message
      const userMsg: ChatMessage = {
        id: `msg-${Date.now()}`,
        role: "user",
        content: text,
        timestamp: new Date().toISOString(),
      };

      setLocalMessages((prev) => [...prev, userMsg]);
      if (!retryContent) setInput("");
      setLocalTyping(true);

      // Create placeholder assistant message for streaming
      const assistantId = `msg-${Date.now() + 1}`;
      const assistantPlaceholder: ChatMessage = {
        id: assistantId,
        role: "assistant",
        content: "",
        timestamp: new Date().toISOString(),
      };

      setLocalMessages((prev) => [...prev, assistantPlaceholder]);
      setIsStreaming(true);

      // Try streaming first, then fall back to non-streaming
      try {
        let gotAnyContent = false;
        let streamedContent = "";
        // What the backend did with a personal detail in this message, if any.
        // (A holder, not a bare let: it is written inside the stream callback.)
        const privacyRef: { current?: StreamMeta["privacy"] } = {};
        await new Promise<void>((resolve, reject) => {
          // Agent turns (tool calls + qwen) can take 30s+ and the backend
          // emits nothing until done — a short timeout here caused the
          // message to be re-sent via the fallback, executing agent actions
          // TWICE and swapping the visible response when the late stream
          // finally arrived. Be patient.
          const timeout = setTimeout(() => {
            if (!gotAnyContent) {
              reject(new Error("Stream timeout"));
            }
          }, 120_000);

          streamMessage(
            text,
            convId,
            (chunk) => {
              gotAnyContent = true;
              streamedContent += chunk;
              clearTimeout(timeout);
              setLocalMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId
                    ? { ...m, content: m.content + chunk }
                    : m
                )
              );
            },
            (meta?: StreamMeta) => {
              clearTimeout(timeout);
              if (gotAnyContent) {
                // Attach sources and suggestions from the final stream chunk
                if (meta) {
                  privacyRef.current = meta.privacy;
                  setLocalMessages((prev) =>
                    prev.map((m) =>
                      m.id === assistantId
                        ? {
                            ...m,
                            sources: meta.sources,
                            suggestions: meta.suggestions,
                            privacyNotice: meta.privacy?.notice,
                            chatNotice: meta.chatNotice,
                          }
                        : m.id === userMsg.id && meta.privacy?.redactedMessage
                          ? { ...m, content: meta.privacy.redactedMessage }
                          : m
                    )
                  );
                }
                resolve();
              } else {
                reject(new Error("Stream ended with no content"));
              }
            },
            {
              // The tool loop's steps: a plain line per tool, and a question
              // that waits for her tap. Either means the bot is working, so
              // the "no answer" timer stops.
              onProgress: (line) => {
                clearTimeout(timeout);
                setLocalMessages((prev) =>
                  prev.map((m) =>
                    m.id === assistantId
                      ? { ...m, progress: [...(m.progress || []), line] }
                      : m
                  )
                );
              },
              onAsk: (ask) => {
                clearTimeout(timeout);
                setLocalMessages((prev) =>
                  prev.map((m) => (m.id === assistantId ? { ...m, ask } : m))
                );
              },
              onReplace: (whole) => {
                streamedContent = whole;
                setLocalMessages((prev) =>
                  prev.map((m) => (m.id === assistantId ? { ...m, content: whole } : m))
                );
              },
              onAnswered: (id, yes) => {
                setLocalMessages((prev) =>
                  prev.map((m) =>
                    m.id === assistantId && m.ask?.id === id
                      ? { ...m, ask: { ...m.ask, answered: yes } }
                      : m
                  )
                );
              },
              onOpen: (urls) => {
                setLocalMessages((prev) =>
                  prev.map((m) =>
                    m.id === assistantId
                      ? { ...m, opens: Array.from(new Set([...(m.opens || []), ...urls])).slice(0, 3) }
                      : m
                  )
                );
              },
              onMissing: (card) => {
                setLocalMessages((prev) =>
                  prev.map((m) => (m.id === assistantId ? { ...m, missing: card } : m))
                );
              },
              onFix: (fix) => {
                setLocalMessages((prev) =>
                  prev.map((m) =>
                    m.id === assistantId ? { ...m, fixes: [...(m.fixes || []), fix].slice(0, 3) } : m
                  )
                );
              },
            }
          );
        });

        setLocalTyping(false);
        setIsStreaming(false);
        setConnectionOk(true);

        // Voice-first: spoken question → spoken answer (avatar lipsyncs it
        // when the overlay is visible)
        if (voiceTurnRef.current && streamedContent) {
          voiceTurnRef.current = false;
          speakResponse(streamedContent);
        }

        // Persist the streamed conversation and refresh history. The redacted
        // copy is saved when the stream returned one; the backend scans again.
        const privacy = privacyRef.current;
        saveChat(convId, privacy?.redactedMessage ?? text, streamedContent)
          .then((saved) => {
            if (!saved || privacy) return;
            setLocalMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId ? { ...m, privacyNotice: saved.notice } : m
              )
            );
          })
          .catch(() => {});
        dispatch(fetchChatHistory());
      } catch (err) {
        // TIMEOUT ≠ failure: the backend may still be mid-tool-call. Never
        // auto-resend on timeout — that executes agent actions twice. Show
        // the manual retry affordance instead.
        if (err instanceof Error && err.message === "Stream timeout") {
          setLocalMessages((prev) => prev.filter((m) => m.id !== assistantId));
          setFailedMessageId(userMsg.id);
          setLocalTyping(false);
          setIsStreaming(false);
          return;
        }
        // Streaming failed outright (endpoint unreachable/errored) —
        // non-streaming fallback is safe because nothing was processed.
        try {
          const result = await sendMessage(text, convId);
          setLocalMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? {
                    ...m,
                    content: result.response,
                    timestamp: result.timestamp,
                    sources: result.sources,
                    suggestions: result.suggestions,
                    privacyNotice: result.privacy?.notice,
                    chatNotice: result.chatNotice,
                  }
                : m.id === userMsg.id && result.privacy?.redactedMessage
                  ? { ...m, content: result.privacy.redactedMessage }
                  : m
            )
          );
          dispatch(setActiveConversation(result.conversation_id));
          setLocalTyping(false);
          setIsStreaming(false);
          setConnectionOk(true);
          // Refresh history after non-streaming too
          dispatch(fetchChatHistory());
        } catch {
          // Both failed — backend offline
          setLocalMessages((prev) =>
            prev.filter((m) => m.id !== assistantId)
          );
          setFailedMessageId(userMsg.id);
          setLocalTyping(false);
          setIsStreaming(false);
          setConnectionOk(false);
        }
      }
    },
    [input, localTyping, isStreaming, activeConversationId, dispatch]
  );

  // Quick-search handoff: the navbar search box stashes an unmatched query in
  // sessionStorage and routes here. Read it once, clear it, and send it as a
  // normal chat turn so voice-first users can "search" straight into a chat.
  const pendingHandledRef = useRef(false);
  useEffect(() => {
    if (pendingHandledRef.current) return;
    let pending: string | null = null;
    try {
      pending = sessionStorage.getItem("puffpai.pendingChatQuery");
    } catch {
      pending = null;
    }
    if (pending && pending.trim()) {
      try {
        sessionStorage.removeItem("puffpai.pendingChatQuery");
      } catch {
        /* ignore */
      }
      pendingHandledRef.current = true;
      handleSend(pending);
    }
  }, [handleSend]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      // While Puff is answering you can keep typing, but Enter must not fire a
      // second request — the keystroke is simply ignored and the draft stays.
      if (disableInput) return;
      handleSend();
    }
  };

  const handleNewChat = () => {
    setLocalMessages([{ ...WELCOME, timestamp: new Date().toISOString() }]);
    dispatch(clearMessages());
    setInput("");
    setFailedMessageId(null);
  };

  const handleLoadConversation = (convId: string) => {
    const conv = conversations.find((c) => c.id === convId);
    if (conv) {
      dispatch(setActiveConversation(convId));
      setLocalMessages(
        conv.messages.length > 0
          ? conv.messages
          : [
              {
                id: "msg-welcome",
                role: "assistant",
                content: greeting,
                timestamp: new Date().toISOString(),
              },
            ]
      );
      setFailedMessageId(null);
    }
  };

  const handleCopy = async (msgId: string, content: string) => {
    try {
      await navigator.clipboard.writeText(content);
      setCopiedId(msgId);
      setTimeout(() => setCopiedId(null), 2000);
    } catch {
      /* clipboard not available */
    }
  };

  const handleRetry = () => {
    if (!failedMessageId) return;
    const failedMsg = localMessages.find((m) => m.id === failedMessageId);
    if (failedMsg) {
      // Remove the failed message and re-send
      setLocalMessages((prev) => prev.filter((m) => m.id !== failedMessageId));
      setFailedMessageId(null);
      handleSend(failedMsg.content);
    }
  };

  // ── Voice: start/stop recording ─────────────────────────────
  const handleMicToggle = useCallback(() => {
    if (isRecording) {
      // Stop recording
      listenRef.current?.stop();
      listenRef.current = null;
      setIsRecording(false);
      dispatch(setIsListening(false));
      return;
    }

    // Start recording
    setIsRecording(true);
    dispatch(setIsListening(true));

    const controller = listen(
      (transcript) => {
        // Got transcription — put it in input and auto-send
        setIsRecording(false);
        dispatch(setIsListening(false));
        listenRef.current = null;

        if (transcript.trim()) {
          setInput(transcript);
          // Voice-first: remember this turn was SPOKEN so the response is
          // spoken back automatically (through the avatar when visible).
          voiceTurnRef.current = true;
          // Auto-send after a short delay so state updates
          setTimeout(() => {
            handleSend(transcript);
          }, 100);
        }
      },
      (error) => {
        console.warn("Voice recognition error:", error);
        setIsRecording(false);
        dispatch(setIsListening(false));
        listenRef.current = null;
      },
      () => {
        setIsRecording(false);
        dispatch(setIsListening(false));
        listenRef.current = null;
      }
    );

    listenRef.current = controller;
  }, [isRecording, dispatch, handleSend]);

  // ── Voice: speak a response ───────────────────────────────
  const speakResponse = useCallback(
    async (text: string) => {
      setIsSpeaking(true);
      dispatch(setAvatarSpeaking(true));
      await speak(
        text,
        () => {
          setIsSpeaking(true);
          dispatch(setAvatarSpeaking(true));
        },
        () => {
          setIsSpeaking(false);
          dispatch(setAvatarSpeaking(false));
        }
      );
    },
    [dispatch]
  );

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      listenRef.current?.stop();
      browserSpeakStop();
      dispatch(setAvatarSpeaking(false));
      dispatch(setIsListening(false));
    };
  }, [dispatch]);

  const disableInput = localTyping || isStreaming;

  // Keep the caret in the composer. Anything that steals focus mid-conversation
  // (the send/mic buttons, a re-render while streaming, the media frame booting)
  // hands it back the moment Puff finishes, so the next message is always
  // type-ready. Skipped when focus is somewhere deliberate — another field, or
  // a control the user is actually operating.
  useEffect(() => {
    if (disableInput) return;
    const active = document.activeElement as HTMLElement | null;
    const parked =
      !active ||
      active === document.body ||
      active === textareaRef.current ||
      active.dataset?.puffRefocus === "1";
    if (parked) textareaRef.current?.focus();
  }, [disableInput]);

  // Outside services that are on, from /system/privacy (the live switches).
  // null = not known, shown as unknown; this row used to say "not used"
  // whatever was switched on.
  const [outsideOn, setOutsideOn] = useState<number | null>(null);
  useEffect(() => {
    let alive = true;
    apiGet<{ outside_services_on?: number | null }>("/system/privacy")
      .then((r) => {
        if (alive && typeof r?.outside_services_on === "number") {
          setOutsideOn(r.outside_services_on);
        }
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  // Real right-rail counts (never fabricated)
  const docCount =
    libraries?.filter((l) => l.is_upload === true || l.content_type === "uploads")
      .length ?? null;
  const packCount =
    libraries === null || docCount === null ? null : libraries.length - docCount;

  const model = systemStatus?.inference?.model;

  return (
    <section
      className="puff-chat grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_clamp(240px,22vw,380px)] lg:gap-[clamp(14px,1.6vw,24px)]"
      style={{
        padding: "clamp(16px,2.4vh,26px) clamp(18px,2vw,36px) clamp(18px,2.8vh,30px)",
        height: "calc(100vh - 7.5rem)",
        minHeight: 540,
      }}
    >
      {/* The legacy `.dark textarea` rule paints form fields slate-800
          !important; this page is themed by --puff vars, so neutralise it. */}
      <style>{`
        .puff-chat textarea {
          background-color: transparent !important;
          color: var(--text) !important;
          border-color: transparent !important;
        }
        .puff-chat textarea::placeholder { color: var(--text3) !important; }
      `}</style>
      {/* ── Main conversation card ─────────────────────────── */}
      <div
        className="flex min-h-0 min-w-0 flex-col overflow-hidden"
        style={{
          borderRadius: 32,
          border: "1px solid var(--line)",
          background: "var(--card)",
          backdropFilter: "blur(18px)",
          boxShadow: "var(--shadow)",
        }}
      >
        {/* Header */}
        <div
          className="flex flex-none items-center gap-3.5"
          style={{ padding: "16px 24px", borderBottom: "1px solid var(--line)" }}
        >
          <img
            src="/puffpai-logo.png"
            alt=""
            style={{
              width: 48,
              height: 48,
              borderRadius: 16,
              objectFit: "contain",
              background: "var(--chip)",
              padding: 4,
            }}
          />
          <div>
            <div style={{ font: `600 22px ${FREDOKA}`, color: "var(--text)" }}>
              {aiName}
            </div>
            <div
              style={{
                font: `700 12px ${JBM}`,
                color:
                  connectionOk === true
                    ? "var(--mint)"
                    : connectionOk === false
                    ? "#E5484D"
                    : "var(--text3)",
              }}
            >
              {connectionOk === true
                ? `ONLINE${model ? ` · ${model}` : ""}`
                : connectionOk === false
                ? "OFFLINE"
                : "CHECKING…"}
            </div>
          </div>
          <div className="flex-1" />
          {/* Always in sight: a long chat is never the owner's problem to
              diagnose, and starting over is one click. */}
          <button
            onClick={handleNewChat}
            className="flex flex-none cursor-pointer items-center gap-1.5 transition hover:opacity-80"
            style={{
              font: `700 14px ${NUNITO}`,
              color: "var(--text)",
              background: "var(--card2)",
              border: "1px solid var(--line)",
              borderRadius: 99,
              padding: "7px 14px 7px 10px",
            }}
            title="Start a new chat"
          >
            <MsIcon name="add" size={18} />
            New chat
          </button>
          <div
            className="hidden items-center gap-2 md:flex"
            style={{ font: `500 15px ${NUNITO}`, color: "var(--text2)" }}
          >
            <span
              style={{
                width: 7,
                height: 7,
                borderRadius: "50%",
                background: "var(--magenta)",
              }}
            />
            Nothing leaves your computer without your consent
          </div>
        </div>

        {/* Backend offline banner */}
        {connectionOk === false && (
          <div
            className="mx-6 mt-4 flex flex-none items-center gap-3"
            style={{
              padding: "12px 18px",
              borderRadius: 18,
              border: "1px solid var(--line)",
              background: "var(--chip)",
            }}
          >
            <span
              style={{
                width: 9,
                height: 9,
                borderRadius: "50%",
                background: "var(--lemon)",
                flex: "none",
              }}
            />
            <div style={{ font: `600 14px ${NUNITO}`, color: "var(--text2)" }}>
              {aiName} is sleeping. Start the backend to chat:{" "}
              <code
                style={{
                  font: `600 12px ${JBM}`,
                  color: "var(--lemon)",
                  background: "var(--chip)",
                  padding: "2px 6px",
                  borderRadius: 8,
                }}
              >
                python -m puffpai.server
              </code>
            </div>
          </div>
        )}

        {/* Messages */}
        <div
          className="flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto"
          style={{ padding: "24px 28px" }}
        >
          {localMessages.map((msg) =>
            msg.role === "user" ? (
              <div key={msg.id} className="group relative max-w-[70%] self-end">
                <div
                  title={formatTime(msg.timestamp)}
                  style={{
                    padding: "16px 22px",
                    borderRadius: "26px 26px 8px 26px",
                    background:
                      "linear-gradient(140deg, var(--pink), var(--magenta))",
                    color: "#fff",
                    font: `600 clamp(15px,1.1vw,19px)/1.45 ${NUNITO}`,
                    whiteSpace: "pre-wrap",
                  }}
                >
                  {msg.content}
                </div>

                {/* Hover actions */}
                <div className="absolute -left-2 -top-2 flex gap-1 opacity-0 transition group-hover:opacity-100">
                  <HoverAction
                    icon={copiedId === msg.id ? "check" : "content_copy"}
                    color={copiedId === msg.id ? "var(--mint)" : "var(--text3)"}
                    title="Copy message"
                    onClick={() => handleCopy(msg.id, msg.content)}
                  />
                </div>

                {/* Retry for failed messages */}
                {failedMessageId === msg.id && (
                  <div className="mt-2 flex items-center justify-end gap-2">
                    <span
                      className="flex items-center gap-1"
                      style={{ font: `600 13px ${NUNITO}`, color: "#E5484D" }}
                    >
                      <MsIcon name="error" size={16} />
                      Failed to send
                    </span>
                    <button
                      onClick={handleRetry}
                      className="flex cursor-pointer items-center gap-1"
                      style={{
                        font: `700 13px ${NUNITO}`,
                        color: "var(--text2)",
                        background: "var(--card2)",
                        border: "1px solid var(--line)",
                        borderRadius: 99,
                        padding: "4px 12px",
                      }}
                    >
                      <MsIcon name="refresh" size={15} />
                      Retry
                    </button>
                  </div>
                )}
              </div>
            ) : (
              <div key={msg.id} className="group relative max-w-[76%] self-start">
                <div
                  title={formatTime(msg.timestamp)}
                  style={{
                    padding: "16px 22px",
                    borderRadius: "26px 26px 26px 8px",
                    background: "var(--card2)",
                    border: "1px solid var(--line)",
                    font: `500 clamp(15px,1.1vw,19px)/1.5 ${NUNITO}`,
                    color: "var(--text)",
                  }}
                >
                  <ToolSteps
                    progress={msg.progress}
                    ask={msg.ask}
                    opens={msg.opens}
                    missing={msg.missing}
                    fixes={msg.fixes}
                    onFixed={() => {
                      // The fix worked: ask her last question again.
                      const at = localMessages.findIndex((m) => m.id === msg.id);
                      const asked = localMessages.slice(0, at).reverse().find((m) => m.role === "user");
                      if (asked) handleSend(asked.content);
                    }}
                    onAnswer={(id, yes) => {
                      approveAsk(id, yes).then((ok) =>
                        setLocalMessages((prev) =>
                          prev.map((m) =>
                            m.id === msg.id && m.ask?.id === id
                              ? { ...m, ask: { ...m.ask, answered: ok ? yes : false } }
                              : m
                          )
                        )
                      );
                    }}
                  />
                  <MarkdownContent content={msg.content} />
                  {/* Pulsing cursor while streaming */}
                  {isStreaming &&
                    msg.id !== "msg-welcome" &&
                    msg === localMessages[localMessages.length - 1] && (
                      <span
                        className="puff-live ml-0.5 inline-block h-4 w-1.5 animate-pulse rounded-sm"
                        style={{ background: "var(--pink)" }}
                      />
                    )}
                </div>

                {/* Privacy notice: the backend's own sentence, verbatim */}
                {msg.privacyNotice && (
                  <div
                    className="mt-2 flex items-start gap-2 pl-1"
                    role="status"
                    style={{ font: `600 14px ${NUNITO}`, color: "var(--text2)" }}
                  >
                    <MsIcon name="lock" size={16} style={{ color: "var(--magenta)", marginTop: 2 }} />
                    <span>{msg.privacyNotice}</span>
                  </div>
                )}

                {/* Long chat: said once, with the way out right next to it */}
                {msg.chatNotice && (
                  <div
                    className="mt-2 flex items-start gap-2 pl-1"
                    role="status"
                    style={{ font: `600 14px ${NUNITO}`, color: "var(--text2)" }}
                  >
                    <MsIcon name="history" size={16} style={{ color: "var(--sky)", marginTop: 2, flex: "none" }} />
                    <span>
                      {msg.chatNotice}{" "}
                      <button
                        onClick={handleNewChat}
                        className="ml-1 cursor-pointer align-baseline transition hover:opacity-80"
                        style={{
                          font: `700 13px ${NUNITO}`,
                          color: "var(--text)",
                          background: "var(--card2)",
                          border: "1px solid var(--line)",
                          borderRadius: 99,
                          padding: "3px 12px",
                        }}
                      >
                        New chat
                      </button>
                    </span>
                  </div>
                )}

                {/* Provenance: real per-message sources from the backend */}
                {msg.sources && msg.sources.length > 0 && (
                  <div
                    className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 pl-1"
                    style={{ font: `500 14px ${NUNITO}`, color: "var(--text3)" }}
                  >
                    <span
                      style={{
                        width: 6,
                        height: 6,
                        borderRadius: "50%",
                        background: "var(--magenta)",
                        flex: "none",
                      }}
                    />
                    <span>
                      Answered from {dedupeSources(msg.sources).length}{" "}
                      {dedupeSources(msg.sources).length === 1 ? "source" : "sources"}:
                    </span>
                    {dedupeSources(msg.sources).map((src, idx) => (
                      <a
                        key={idx}
                        href={`/dashboard/knowledge?collection=${encodeURIComponent(src.collection)}`}
                        className="underline-offset-2 hover:underline"
                        style={{ color: "var(--sky)", fontWeight: 700 }}
                        title={`View in Knowledge: ${src.library}`}
                      >
                        {src.library}
                      </a>
                    ))}
                  </div>
                )}

                {/* Follow-up suggestions */}
                {msg.suggestions && msg.suggestions.length > 0 && !isStreaming && (
                  <div className="mt-2 flex flex-wrap gap-2">
                    {msg.suggestions.map((q, idx) => (
                      <button
                        key={idx}
                        onClick={() => handleSend(q)}
                        disabled={disableInput}
                        className="cursor-pointer text-left transition hover:opacity-80 disabled:opacity-40"
                        style={{
                          font: `600 13px ${NUNITO}`,
                          color: "var(--text2)",
                          background: "var(--card2)",
                          border: "1px solid var(--line)",
                          borderRadius: 99,
                          padding: "8px 16px",
                        }}
                      >
                        {q}
                      </button>
                    ))}
                  </div>
                )}

                {/* Hover actions */}
                <div className="absolute -right-2 -top-2 flex gap-1 opacity-0 transition group-hover:opacity-100">
                  <HoverAction
                    icon={copiedId === msg.id ? "check" : "content_copy"}
                    color={copiedId === msg.id ? "var(--mint)" : "var(--text3)"}
                    title="Copy message"
                    onClick={() => handleCopy(msg.id, msg.content)}
                  />
                  {msg.content && msg.id !== "msg-welcome" && (
                    <HoverAction
                      icon="volume_up"
                      color={isSpeaking ? "var(--sky)" : "var(--text3)"}
                      title={isSpeaking ? "Speaking…" : "Listen to response"}
                      onClick={() => !isSpeaking && speakResponse(msg.content)}
                    />
                  )}
                </div>
              </div>
            )
          )}

          {/* Typing dots (waiting, not yet streaming) */}
          {localTyping && !isStreaming && (
            <div
              className="flex items-center gap-2.5 self-start"
              style={{
                padding: "16px 22px",
                borderRadius: 26,
                background: "var(--card2)",
                border: "1px solid var(--line)",
              }}
            >
              {[0, 0.15, 0.3].map((d) => (
                <span
                  key={d}
                  style={{
                    width: 10,
                    height: 10,
                    borderRadius: "50%",
                    background: "var(--pink)",
                    display: "inline-block",
                    animation: `puffWave 1s ${d}s infinite`,
                  }}
                />
              ))}
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Composer */}
        <div
          className="flex flex-none items-end gap-3"
          style={{ padding: "18px 24px", borderTop: "1px solid var(--line)" }}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf,.docx,.txt,.md,.csv,.xlsx,.png,.jpg,.jpeg"
            className="hidden"
            onChange={handleFileUpload}
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={uploadingFile}
            className="grid flex-none cursor-pointer place-items-center transition hover:opacity-80 disabled:opacity-50"
            style={{
              width: 52,
              height: 52,
              borderRadius: 18,
              border: "1px solid var(--line)",
              background: "var(--card2)",
              color: "var(--text2)",
              marginBottom: 8,
            }}
            title="Attach a file so I can answer questions about it"
          >
            <MsIcon
              name="attach_file"
              size={24}
              className={uploadingFile ? "animate-pulse" : ""}
            />
          </button>

          <div
            className="flex min-w-0 flex-1 items-center"
            style={{
              minHeight: 68,
              borderRadius: 22,
              background: "var(--card2)",
              border: "1px solid var(--line)",
              padding: "10px 24px",
            }}
          >
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={
                disableInput ? `${aiName} is thinking…` : `Ask ${aiName} anything…`
              }
              rows={1}
              /* Deliberately NOT disabled while Puff answers: a disabled
                 element is blurred by the browser and never regains focus, so
                 the caret vanished after every message and you had to click
                 back in. Enter is gated in handleKeyDown and the send button
                 stays disabled, so nothing can double-fire — you can just
                 keep typing your next question. */
              autoFocus
              className="w-full resize-none bg-transparent outline-none disabled:cursor-not-allowed"
              style={{
                font: `500 clamp(15px,1.1vw,19px)/1.4 ${NUNITO}`,
                color: "var(--text)",
              }}
            />
          </div>

          {/* Voice input (browser STT → auto-send, reply spoken back) */}
          <button
            onClick={handleMicToggle}
            disabled={!voiceEnabled || disableInput}
            className="puff-live grid flex-none cursor-pointer place-items-center transition hover:opacity-85 disabled:cursor-not-allowed disabled:opacity-30"
            style={{
              width: 68,
              height: 68,
              borderRadius: 22,
              border: "1px solid var(--line)",
              background: isRecording ? "#E5484D" : "var(--card2)",
              color: isRecording ? "#fff" : "var(--text)",
              boxShadow: isRecording ? "0 0 24px rgba(229,72,77,.5)" : undefined,
              // puff-live: recording feedback must keep blinking even when the
              // idle/kiosk motion governor freezes ambient puffBlink carriers.
              animation: isRecording ? "puffBlink 1.2s infinite" : undefined,
            }}
            title={
              !voiceEnabled
                ? "Voice not supported in this browser"
                : isRecording
                ? "Stop recording"
                : `Talk to ${aiName}`
            }
          >
            <MsIcon name={isRecording ? "stop" : "mic"} size={30} />
          </button>

          {/* Send */}
          <button
            onClick={() => handleSend()}
            data-puff-refocus="1"
            disabled={!input.trim() || disableInput}
            className="grid flex-none cursor-pointer place-items-center transition hover:opacity-90 disabled:opacity-40"
            style={{
              width: 68,
              height: 68,
              borderRadius: 22,
              border: "none",
              background: "linear-gradient(140deg, var(--pink), var(--magenta))",
              color: "#fff",
              boxShadow: "0 10px 28px var(--glow1)",
            }}
            title="Send message"
          >
            <MsIcon name="arrow_upward" size={30} />
          </button>
        </div>
      </div>

      {/* ── Right rail ─────────────────────────────────────── */}
      <div className="flex min-h-0 flex-col gap-[18px] overflow-hidden">
        {/* What Puff used — live zone counts, never per-message claims */}
        <div
          style={{
            borderRadius: 28,
            border: "1px solid var(--line)",
            background: "var(--card)",
            backdropFilter: "blur(18px)",
            padding: "22px 24px",
          }}
        >
          <div
            style={{
              font: `600 19px ${FREDOKA}`,
              color: "var(--text)",
              marginBottom: 16,
            }}
          >
            What {aiName} can use
          </div>
          <div className="flex flex-col gap-3">
            <RailSource
              icon="lock"
              iconColor="var(--magenta)"
              title="My Documents"
              stat={docCount === null ? null : `${docCount} ${docCount === 1 ? "doc" : "docs"} · local`}
            />
            <RailSource
              icon="menu_book"
              iconColor="var(--sky)"
              title="Knowledge Base"
              stat={packCount === null ? null : `${packCount} ${packCount === 1 ? "pack" : "packs"} · offline`}
            />
            <RailSource
              icon="public"
              iconColor="var(--lemon)"
              title="Outside services"
              stat={outsideOn === null ? null : outsideOn === 0 ? "all off" : `${outsideOn} on`}
              dimmed={outsideOn === 0}
            />
          </div>
        </div>

        {/* Recent — real conversations */}
        <div
          className="flex min-h-0 flex-1 flex-col overflow-hidden"
          style={{
            borderRadius: 28,
            border: "1px solid var(--line)",
            background: "var(--card)",
            backdropFilter: "blur(18px)",
            padding: "22px 24px",
          }}
        >
          <div className="mb-4 flex flex-none items-center">
            <div style={{ font: `600 19px ${FREDOKA}`, color: "var(--text)" }}>
              Recent
            </div>
            <div className="flex-1" />
            <button
              onClick={handleNewChat}
              className="grid cursor-pointer place-items-center transition hover:opacity-80"
              style={{
                width: 34,
                height: 34,
                borderRadius: 12,
                border: "1px solid var(--line)",
                background: "var(--card2)",
                color: "var(--text2)",
              }}
              title="New chat"
            >
              <MsIcon name="add" size={20} />
            </button>
          </div>
          <div className="flex min-h-0 flex-1 flex-col gap-2.5 overflow-y-auto">
            {conversations.length === 0 ? (
              <div
                className="grid place-items-center"
                style={{
                  minHeight: 90,
                  borderRadius: 16,
                  background: STRIPES,
                  font: `600 12px ${JBM}`,
                  color: "var(--text3)",
                  textAlign: "center",
                  padding: "0 12px",
                }}
              >
                no conversations yet,<br />
                start chatting
              </div>
            ) : (
              conversations.map((conv) => (
                <button
                  key={conv.id}
                  onClick={() => handleLoadConversation(conv.id)}
                  className="w-full flex-none cursor-pointer text-left transition hover:opacity-85"
                  style={{
                    padding: "14px 16px",
                    borderRadius: 16,
                    background: "var(--card2)",
                    border:
                      activeConversationId === conv.id
                        ? "1px solid var(--pink)"
                        : "1px solid transparent",
                    font: `600 15px ${NUNITO}`,
                    color:
                      activeConversationId === conv.id
                        ? "var(--text)"
                        : "var(--text2)",
                  }}
                >
                  <span className="block truncate">{conversationTitle(conv)}</span>
                </button>
              ))
            )}
          </div>
        </div>
      </div>

      {/* Floating avatar overlay */}
      <AvatarOverlay />
    </section>
  );
}

/* ---------- small design components ---------- */

function HoverAction({
  icon,
  color,
  title,
  onClick,
}: {
  icon: string;
  color: string;
  title: string;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      className="grid cursor-pointer place-items-center shadow-md"
      style={{
        width: 30,
        height: 30,
        borderRadius: "50%",
        background: "var(--card)",
        border: "1px solid var(--line)",
        color,
        backdropFilter: "blur(10px)",
      }}
    >
      <MsIcon name={icon} size={16} />
    </button>
  );
}

function RailSource({
  icon,
  iconColor,
  title,
  stat,
  dimmed = false,
}: {
  icon: string;
  iconColor: string;
  title: string;
  /** null = not loaded / unavailable → striped placeholder, never a fake number */
  stat: string | null;
  dimmed?: boolean;
}) {
  return (
    <div
      className="flex items-center gap-3"
      style={{
        padding: 14,
        borderRadius: 18,
        background: "var(--card2)",
        border: "1px solid var(--line)",
        opacity: dimmed ? 0.5 : 1,
      }}
    >
      <MsIcon name={icon} size={22} style={{ color: iconColor }} />
      <div className="min-w-0 flex-1">
        <div style={{ font: `700 15px ${NUNITO}`, color: "var(--text)" }}>
          {title}
        </div>
        {stat === null ? (
          <div
            style={{
              marginTop: 4,
              height: 12,
              width: 90,
              borderRadius: 6,
              background: STRIPES,
            }}
            title="Counts cannot be read right now"
          />
        ) : (
          <div style={{ font: `600 12px ${JBM}`, color: "var(--text3)" }}>
            {stat}
          </div>
        )}
      </div>
    </div>
  );
}

/* ---------- helpers ---------- */

/** One entry per book or library: every downloaded book shares the
 *  "books" collection, so the name tells them apart (otherwise
 *  "Answered from 3 sources" lists one). */
function dedupeSources<T extends { collection: string; library?: string }>(sources: T[]): T[] {
  const seen = new Set<string>();
  return sources.filter((s) => {
    const key = s.library || s.collection;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function formatTime(isoOrTime: string): string {
  try {
    const d = new Date(isoOrTime);
    if (isNaN(d.getTime())) return isoOrTime;
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return isoOrTime;
  }
}

/* ---------- Simple Markdown renderer ---------- */

function MarkdownContent({ content }: { content: string }) {
  if (!content) return null;

  // Split by code blocks first
  const parts = content.split(/(```[\s\S]*?```)/g);

  return (
    <>
      {parts.map((part, i) => {
        // Code block
        if (part.startsWith("```") && part.endsWith("```")) {
          const inner = part.slice(3, -3);
          const newlineIdx = inner.indexOf("\n");
          const lang = newlineIdx > 0 ? inner.slice(0, newlineIdx).trim() : "";
          const code = newlineIdx > 0 ? inner.slice(newlineIdx + 1) : inner;
          return (
            <pre
              key={i}
              className="my-2 overflow-x-auto"
              style={{
                borderRadius: 14,
                background: "var(--chip)",
                border: "1px solid var(--line)",
                padding: 14,
                font: `500 13px/1.5 var(--font-jbmono), monospace`,
                color: "var(--text)",
              }}
            >
              {lang && (
                <div
                  className="mb-1 uppercase"
                  style={{
                    font: `700 10px var(--font-jbmono), monospace`,
                    letterSpacing: ".14em",
                    color: "var(--text3)",
                  }}
                >
                  {lang}
                </div>
              )}
              <code>{code}</code>
            </pre>
          );
        }

        // Regular text with inline formatting
        return <InlineMarkdown key={i} text={part} />;
      })}
    </>
  );
}

function InlineMarkdown({ text }: { text: string }) {
  const lines = text.split("\n");
  return (
    <>
      {lines.map((line, li) => {
        // Headings
        if (line.startsWith("### "))
          return (
            <h4
              key={li}
              className="mb-1 mt-2"
              style={{ font: `600 1.05em ${FREDOKA}` }}
            >
              {line.slice(4)}
            </h4>
          );
        if (line.startsWith("## "))
          return (
            <h3
              key={li}
              className="mb-1 mt-2"
              style={{ font: `600 1.12em ${FREDOKA}` }}
            >
              {line.slice(3)}
            </h3>
          );
        if (line.startsWith("# "))
          return (
            <h2
              key={li}
              className="mb-1 mt-2"
              style={{ font: `600 1.2em ${FREDOKA}` }}
            >
              {line.slice(2)}
            </h2>
          );

        // Unordered list
        if (line.match(/^[-*]\s/))
          return (
            <div key={li} className="ml-4 flex gap-1.5">
              <span style={{ color: "var(--pink)" }}>{"\u{2022}"}</span>
              <span>{renderInline(line.slice(2))}</span>
            </div>
          );

        // Ordered list
        const olMatch = line.match(/^(\d+)\.\s(.*)/);
        if (olMatch)
          return (
            <div key={li} className="ml-4 flex gap-1.5">
              <span className="min-w-[1.25rem]" style={{ color: "var(--pink)" }}>
                {olMatch[1]}.
              </span>
              <span>{renderInline(olMatch[2])}</span>
            </div>
          );

        // Empty line
        if (!line.trim()) return <br key={li} />;

        // Normal paragraph
        return <p key={li}>{renderInline(line)}</p>;
      })}
    </>
  );
}

function renderInline(text: string): React.ReactNode {
  // Bold, inline code, italic
  const parts: React.ReactNode[] = [];
  let remaining = text;
  let key = 0;

  while (remaining.length > 0) {
    // Inline code
    const codeMatch = remaining.match(/^(.*?)`([^`]+)`([\s\S]*)/);
    if (codeMatch) {
      if (codeMatch[1]) parts.push(renderBoldItalic(codeMatch[1], key++));
      parts.push(
        <code
          key={key++}
          style={{
            font: `600 0.85em var(--font-jbmono), monospace`,
            background: "var(--chip)",
            padding: "1px 5px",
            borderRadius: 6,
          }}
        >
          {codeMatch[2]}
        </code>
      );
      remaining = codeMatch[3];
      continue;
    }
    // No more matches
    parts.push(renderBoldItalic(remaining, key++));
    break;
  }

  return <>{parts}</>;
}

function renderBoldItalic(text: string, key: number): React.ReactNode {
  // **bold**
  const segments = text.split(/(\*\*[^*]+\*\*)/g);
  return (
    <React.Fragment key={`bi-${key}`}>
      {segments.map((seg, i) => {
        if (seg.startsWith("**") && seg.endsWith("**")) {
          return (
            <strong key={`${key}-b-${i}`} style={{ color: "var(--pink)" }}>
              {seg.slice(2, -2)}
            </strong>
          );
        }
        // *italic*
        const italicParts = seg.split(/(\*[^*]+\*)/g);
        return (
          <React.Fragment key={`${key}-s-${i}`}>
            {italicParts.map((ip, j) => {
              if (ip.startsWith("*") && ip.endsWith("*") && ip.length > 2) {
                return <em key={`${key}-i-${i}-${j}`}>{ip.slice(1, -1)}</em>;
              }
              return <span key={`${key}-t-${i}-${j}`}>{ip}</span>;
            })}
          </React.Fragment>
        );
      })}
    </React.Fragment>
  );
}
