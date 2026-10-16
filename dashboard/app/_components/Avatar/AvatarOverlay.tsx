"use client";

/**
 * Floating compact avatar overlay for the chat page.
 * Draggable, minimizable, shows the 3D avatar synced with conversation state.
 *
 * Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
 */

import { useRef, useState, useCallback, useEffect } from "react";
import { mdiClose, mdiArrowExpand, mdiArrowCollapse } from "@mdi/js";
import Icon from "../Icon";
import { useAppSelector, useAppDispatch } from "@/app/_stores/hooks";
import { setAvatarVisible } from "@/app/_stores/avatarSlice";
import { registerAvatarSink } from "@/app/_lib/services/voiceService";
import AvatarStage from "./AvatarStage";
import type { AvatarEngineHandle } from "./engines/types";
import useAvatarCharacter from "./useAvatarCharacter";
import { useBuildSelf } from "@/app/_lib/hooks/useBuildSelf";
import { assistantName } from "@/app/_lib/surfaceMap";

export default function AvatarOverlay() {
  const avatarRef = useRef<AvatarEngineHandle>(null);
  const character = useAvatarCharacter();
  // The label is the bot that is speaking: the owner's name for it.
  const { build } = useBuildSelf();
  const botName = assistantName(build);
  const dispatch = useAppDispatch();
  const { visible, avatarUrl, mood, speaking, voiceMode, isListening } =
    useAppSelector((state) => state.avatar);

  const [expanded, setExpanded] = useState(false);
  // null until measured against the viewport — rendering before that put the
  // panel at 0,0 (top-left, over the sidebar) for a frame.
  const [position, setPosition] = useState<{ x: number; y: number } | null>(
    null
  );
  const [isDragging, setIsDragging] = useState(false);
  const dragRef = useRef<{ startX: number; startY: number; origX: number; origY: number } | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // Initial position: saved spot if she's been dragged before, otherwise
  // right side ABOVE the chat input bar (she used to sit on top of it).
  // Toggling expand only clamps her back on-screen instead of resetting.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const w = expanded ? 320 : 200;
    const h = expanded ? 340 : 240;
    let saved: { x: number; y: number } | null = null;
    try {
      saved = JSON.parse(localStorage.getItem("puffpai_avatar_pos") || "null");
    } catch {}
    // Default: bottom-right, sitting ABOVE the FloatingPuff bubble (which
    // occupies roughly the bottom 280px of the right edge).
    const x = saved?.x ?? window.innerWidth - w - 24;
    const y = saved?.y ?? window.innerHeight - h - 300;
    // Math.max last so a viewport too small for the clamp keeps the panel
    // on-screen instead of flipping it to negative (top-left over the rail).
    setPosition({
      x: Math.max(8, Math.min(x, window.innerWidth - w - 8)),
      y: Math.max(8, Math.min(y, window.innerHeight - h - 8)),
    });
  }, [expanded]);

  // Remember where she was dropped
  useEffect(() => {
    if (isDragging || !position) return;
    try {
      localStorage.setItem("puffpai_avatar_pos", JSON.stringify(position));
    } catch {}
  }, [isDragging, position]);

  // Voice-first: while the overlay is visible, ALL server-TTS speech routes
  // through her mouth (speakPcm → lipsync) instead of bare audio playback.
  useEffect(() => {
    if (!visible) return;
    registerAvatarSink(async (pcmBase64, sampleRate, text) => {
      const h = avatarRef.current;
      if (!h?.isReady()) throw new Error("avatar not ready");
      await h.speakPcm(pcmBase64, sampleRate, text);
    });
    return () => registerAvatarSink(null);
  }, [visible]);

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if ((e.target as HTMLElement).closest("button")) return;
      if (!position) return;
      setIsDragging(true);
      dragRef.current = {
        startX: e.clientX,
        startY: e.clientY,
        origX: position.x,
        origY: position.y,
      };
    },
    [position]
  );

  useEffect(() => {
    if (!isDragging) return;

    const handleMouseMove = (e: MouseEvent) => {
      if (!dragRef.current) return;
      const dx = e.clientX - dragRef.current.startX;
      const dy = e.clientY - dragRef.current.startY;
      setPosition({
        x: dragRef.current.origX + dx,
        y: dragRef.current.origY + dy,
      });
    };

    const handleMouseUp = () => {
      setIsDragging(false);
      dragRef.current = null;
    };

    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseup", handleMouseUp);
    return () => {
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    };
  }, [isDragging]);

  // The persistent Puff bubble (FloatingPuff) owns idle presence on every
  // page now, so this overlay only earns screen space while the avatar is
  // actually doing something — speaking, listening, or in voice mode. The
  // close button (setAvatarVisible(false)) still suppresses it entirely.
  const active = speaking || isListening || voiceMode;
  if (!visible || !active || !position) return null;

  const width = expanded ? 320 : 200;
  const height = expanded ? 340 : 240;

  return (
    <div
      ref={containerRef}
      className="fixed z-50 select-none"
      style={{
        left: `${position.x}px`,
        top: `${position.y}px`,
        width: `${width}px`,
        height: `${height}px`,
        cursor: isDragging ? "grabbing" : "grab",
      }}
      onMouseDown={handleMouseDown}
    >
      <div className="relative h-full w-full overflow-hidden rounded-2xl border shadow-2xl backdrop-blur-md border-(--line) bg-(--puff-surface-deep)">
        {/* Header controls */}
        <div className="absolute right-2 top-2 z-10 flex gap-1">
          <button
            onClick={() => setExpanded((prev) => !prev)}
            className="rounded-full p-1.5 shadow-sm transition hover:text-(--text) bg-(--puff-surface-deep) text-(--text2) hover:bg-(--puff-card-hover)"
            title={expanded ? "Compact" : "Expand"}
          >
            <Icon
              path={expanded ? mdiArrowCollapse : mdiArrowExpand}
              size="14"
            />
          </button>
          <button
            onClick={() => dispatch(setAvatarVisible(false))}
            className="rounded-full p-1.5 shadow-sm transition hover:text-(--puff-red) bg-(--puff-surface-deep) text-(--text2) hover:bg-(--puff-red)/30"
            title="Hide avatar"
          >
            <Icon path={mdiClose} size="14" />
          </button>
        </div>

        {/* Speaking indicator */}
        {speaking && (
          <div className="absolute left-2 top-2 z-10 flex items-center gap-1.5 rounded-full bg-(--sky)/90 px-2.5 py-1 text-[10px] font-medium text-(--puff-on-accent) shadow-sm backdrop-blur-sm">
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-(--puff-on-accent) opacity-75" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-(--puff-on-accent)" />
            </span>
            Speaking
          </div>
        )}

        {/* Avatar viewport */}
        <AvatarStage
          engineId={character}
          ref={avatarRef}
          avatarUrl={avatarUrl}
          mood={mood}
          className="h-full w-full"
        />

        {/* Bottom label */}
        <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-(--puff-surface-deep)/40 to-transparent px-3 py-2">
          <p className="text-xs font-medium text-(--text) drop-shadow-sm">
            {botName}
          </p>
        </div>
      </div>
    </div>
  );
}
