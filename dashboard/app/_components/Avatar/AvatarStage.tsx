"use client";

/**
 * AvatarStage — renders whichever avatar engine the user picked, behind one
 * imperative handle, with graceful degradation.
 *
 * Placements (tile, overlay, panel) render exactly one <AvatarStage
 * engineId={…} ref={…} …/> instead of hard-coding `character === "puff" ? … :
 * …`. If a heavy engine (Three.js/WebGL) throws while loading — missing WebGL,
 * a bad GLB URL, a chunk that won't download — the stage catches it and falls
 * back to the always-safe Puff SVG engine, so the avatar never blanks out. The
 * caller's ref stays valid across the swap because the fallback implements the
 * same handle.
 *
 * Copyright (c) 2026 Cypher Sisters. Apache-2.0 License.
 */

import {
  Component,
  forwardRef,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { resolveEngine, FALLBACK_ENGINE_ID } from "./engines/registry";
import { DEFAULT_AVATAR_MODEL_URL } from "@/app/_stores/avatarSlice";
import type {
  AvatarEngineHandle,
  AvatarEngineProps,
  AvatarEngineId,
} from "./engines/types";

interface Props extends AvatarEngineProps {
  /** Which engine to render. Unknown ids resolve to the fallback (Puff). */
  engineId: AvatarEngineId | string;
}

/* ── Error boundary: catches engine load/render crashes ─────────────── */

class EngineErrorBoundary extends Component<
  { onError: (message: string) => void; children: ReactNode },
  { crashed: boolean }
> {
  state = { crashed: false };

  static getDerivedStateFromError() {
    return { crashed: true };
  }

  componentDidCatch(error: unknown) {
    const msg = error instanceof Error ? error.message : String(error);
    console.error("[AvatarStage] engine crashed, degrading to fallback:", msg);
    this.props.onError(msg);
  }

  render() {
    if (this.state.crashed) return null; // parent swaps to the fallback engine
    return this.props.children;
  }
}

/* ── Stage ──────────────────────────────────────────────────────────── */

const AvatarStage = forwardRef<AvatarEngineHandle, Props>(function AvatarStage(
  { engineId, onError, ...rest },
  ref
) {
  // When an engine crashes we pin this to the fallback id so the next render
  // mounts Puff (which the ref then attaches to).
  const [degradedTo, setDegradedTo] = useState<AvatarEngineId | null>(null);

  // A fresh engine choice gets a fresh chance — clear any prior degradation.
  useEffect(() => {
    setDegradedTo(null);
  }, [engineId]);

  const activeId = degradedTo ?? engineId;
  const descriptor = resolveEngine(activeId);
  const Engine = descriptor.Component;
  // Model precedence: the user's explicit custom URL (empty when unset) wins,
  // then the engine's own default model, then the global last-resort default.
  const modelUrl =
    rest.avatarUrl || descriptor.defaultModelUrl || DEFAULT_AVATAR_MODEL_URL;

  // No GLB ships with this build, so a 3D engine with no model of the owner's
  // own lands here: it renders the always-safe Puff fallback plus a one-line
  // hint instead of fetching "" and looping on a 404.
  const needsModel = descriptor.capabilities.customModel === "glb";
  if (needsModel && !modelUrl) {
    const Fallback = resolveEngine(FALLBACK_ENGINE_ID).Component;
    return (
      <div className="flex h-full min-h-0 flex-col">
        <div className="min-h-0 flex-1">
          <Fallback ref={ref} {...rest} avatarUrl="" onError={onError} />
        </div>
        <p className="px-2 pb-1 text-center text-[11px] opacity-70">
          No 3D model yet. Upload your own .glb under{" "}
          <a href="/dashboard/profile" className="underline">
            Profile → AI Avatar
          </a>
          . Puff is drawn until then.
        </p>
      </div>
    );
  }

  const engineEl = (
    <Engine ref={ref} {...rest} avatarUrl={modelUrl} onError={onError} />
  );

  // Puff (the fallback) is safe — never wrap it in a boundary; wrapping it would
  // swallow nothing and there is nowhere left to degrade to.
  if (descriptor.id === FALLBACK_ENGINE_ID) return engineEl;

  return (
    <EngineErrorBoundary
      onError={(msg) => {
        onError?.(msg);
        setDegradedTo(FALLBACK_ENGINE_ID);
      }}
    >
      {engineEl}
    </EngineErrorBoundary>
  );
});

export default AvatarStage;
