"use client";

/**
 * TileGrid — the dashboard's arrangeable board (react-grid-layout v2).
 *
 * Replaces the dnd-kit DraggableGrid: that one could only swap
 * tile ORDER, and each tile's col-span travelled with it, so dragging a
 * narrow tile into a wide slot shredded the rows. Here every tile has a real
 * (x, y, w, h) rect on a 12-column grid: drag anywhere, resize from the
 * edges. What is kept is the order and the sizes, in the one layout file on
 * the backend (GET/PUT /dashboard/layout); the parent saves them.
 *
 * Tiles are inert until the parent's "Arrange" toggle sets `editing` — so
 * taps on tile content never move anything by accident (the appliance screen
 * is a touchscreen). While editing, a translucent overlay captures the
 * pointer: the whole tile is a drag surface and its content can't be
 * clicked.
 *
 * Breakpoints are CONTAINER widths (the grid sits beside a 240px sidebar,
 * so a 1280px window gives a ~1000px container — that must still be "lg").
 */

import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Responsive,
  verticalCompactor,
  type Layout,
  type LayoutItem,
  type ResponsiveLayouts,
} from "react-grid-layout";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";

export interface TileDef {
  id: string;
  /** Default rect on the lg (12-col) grid; other breakpoints derive. */
  lg: {
    x: number;
    y: number;
    w: number;
    h: number;
    minW?: number;
    minH?: number;
  };
  node: React.ReactNode;
}

const BREAKPOINTS = { lg: 940, md: 620, sm: 0 };
const COLS = { lg: 12, md: 8, sm: 2 };
const ROW_HEIGHT = 72;
const MARGIN: [number, number] = [10, 10];
/** Below this a fitted row would be unreadable — clamp and let the grid scroll. */
const MIN_FIT_ROW = 44;

/**
 * Flow the tiles, in the order given, onto the lg grid: left to right, a new
 * row when the next one does not fit. The vertical compactor tidies the rest.
 * The layout file keeps only order and sizes (dashboard_widgets.py), so the
 * brain can move a tile by name and every device draws the same board.
 */
function flow(
  tiles: TileDef[],
  sizes: Record<string, { w: number; h: number }>
): ResponsiveLayouts {
  const cols = COLS.lg;
  let x = 0;
  let y = 0;
  let rowH = 0;
  const lg: LayoutItem[] = [];
  for (const tile of tiles) {
    const minW = tile.lg.minW ?? 2;
    const minH = tile.lg.minH ?? 2;
    const w = Math.min(cols, Math.max(minW, sizes[tile.id]?.w ?? tile.lg.w));
    const h = Math.max(minH, sizes[tile.id]?.h ?? tile.lg.h);
    if (x + w > cols) {
      x = 0;
      y += rowH;
      rowH = 0;
    }
    lg.push({ i: tile.id, x, y, w, h, minW, minH });
    x += w;
    rowH = Math.max(rowH, h);
  }
  return { lg };
}

/** Reading order of a layout: top to bottom, then left to right. */
function readingOrder(layout: Layout): string[] {
  return [...layout]
    .sort((a, b) => a.y - b.y || a.x - b.x)
    .map((l) => l.i);
}

export default function TileGrid({
  tiles,
  sizes,
  editing,
  onArrange,
  onRemoveTile,
  editBar,
  fitHeight = false,
}: {
  /** In the owner's order (pinned first); the board flows them in it. */
  tiles: TileDef[];
  /** Her saved sizes from the layout file; a tile without one keeps lg w/h. */
  sizes: Record<string, { w: number; h: number }>;
  editing: boolean;
  /** Called after a drag or resize in arrange mode with the new reading
   *  order and every tile's lg size; the parent saves both. */
  onArrange?: (order: string[], sizes: Record<string, { w: number; h: number }>) => void;
  /** Kiosk mode: derive rowHeight from the measured container height so the
   *  whole board lands on one screen (see the rowHeight memo below). */
  fitHeight?: boolean;
  /** When set, arrange mode shows a remove button on every tile (design's
   *  magenta close bubble); the parent hides the tile + offers it back in
   *  the arrange tray. */
  onRemoveTile?: (id: string) => void;
  /** Arrange mode: the parent's small controls for one tile (pin, rename). */
  editBar?: (id: string) => React.ReactNode;
}) {
  // Own the width measurement (library's useContainerWidth was seen serving
  // its 1280 initialWidth to the first real layout, overflowing the column).
  // Grid renders only after a real measurement exists — never at a guess.
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState<number | null>(null);
  // Height is measured too (kiosk fit). The grid's own height is driven BY
  // rowHeight, so measuring the container would feed back on itself —
  // measure the parent, which is the flex slot the grid is allowed to fill.
  const [height, setHeight] = useState<number | null>(null);
  useEffect(() => {
    const node = containerRef.current;
    if (!node) return;
    const slot = node.parentElement;
    const measure = () => {
      const w = Math.round(node.getBoundingClientRect().width);
      setWidth((prev) => (prev === w ? prev : w));
      if (slot) {
        const h = Math.round(slot.getBoundingClientRect().height);
        setHeight((prev) => (prev === h ? prev : h));
      }
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(node);
    if (slot) ro.observe(slot);
    return () => ro.disconnect();
  }, []);

  // The board is drawn from the order and sizes it is given. While she drags
  // in arrange mode the grid's own layout is kept as she left it; when the
  // order or sizes change from outside (another device, the brain) or arrange
  // mode ends, the board flows again from what the layout file says.
  const tileIds = tiles.map((t) => t.id).join("|");
  const sizeKey = JSON.stringify(sizes);
  const flowed = useMemo(
    () => flow(tiles, sizes),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [tileIds, sizeKey]
  );
  const [live, setLive] = useState<ResponsiveLayouts | null>(null);
  // A tile added or taken away starts the board over from the flow.
  const tileSet = [...tiles.map((t) => t.id)].sort().join("|");
  useEffect(() => {
    setLive(null);
  }, [tileSet]);
  useEffect(() => {
    if (!editing) setLive(null);
  }, [editing, tileIds, sizeKey]);
  const merged = editing && live ? live : flowed;

  const mounted = width !== null;

  /**
   * Kiosk fit: rowHeight is whatever makes the board's total rows land
   * exactly on the measured slot height. Recomputes on resize AND on layout
   * change — dragging a tile down adds a row, and the board then re-fits
   * (shorter rows) instead of pushing content off-screen.
   *
   * Only above the lg breakpoint; smaller windows keep the fixed row height
   * and their normal scrolling.
   */
  const { rowHeight, fitted } = useMemo(() => {
    const isLg = (width ?? 0) >= BREAKPOINTS.lg;
    if (!fitHeight || !isLg || !height) {
      return { rowHeight: ROW_HEIGHT, fitted: false };
    }
    const rows = (merged.lg ?? []).reduce(
      (max, l) => Math.max(max, l.y + l.h),
      0
    );
    if (rows <= 0) return { rowHeight: ROW_HEIGHT, fitted: false };
    const raw = Math.floor((height - MARGIN[1] * (rows - 1)) / rows);
    if (raw < MIN_FIT_ROW) return { rowHeight: MIN_FIT_ROW, fitted: false };
    return { rowHeight: raw, fitted: true };
  }, [fitHeight, width, height, merged]);

  const handleLayoutChange = useCallback(
    (current: Layout, all: ResponsiveLayouts) => {
      // The grid reports its layout on mount too; only her own drags count.
      if (!editing) return;
      setLive(all);
      const order = readingOrder(current);
      const before = readingOrder((flowed.lg ?? []) as Layout);
      const next: Record<string, { w: number; h: number }> = {};
      for (const l of all.lg ?? current) next[l.i] = { w: l.w, h: l.h };
      const sameOrder = order.join("|") === before.join("|");
      const sameSize = (flowed.lg ?? []).every(
        (l) => next[l.i]?.w === l.w && next[l.i]?.h === l.h
      );
      if (!(sameOrder && sameSize)) onArrange?.(order, next);
    },
    [editing, flowed, onArrange]
  );

  return (
    <div
      ref={containerRef}
      className={editing ? "puff-grid-editing" : ""}
      style={
        fitHeight
          ? {
              height: "100%",
              minHeight: 0,
              // Exact fit needs no scroll and must not clip the tile glow;
              // a clamped (too-short) fit falls back to scrolling.
              overflowY: fitted ? "visible" : "auto",
              overflowX: "visible",
            }
          : undefined
      }
    >
      {mounted ? (
        <Responsive
          layouts={merged}
          breakpoints={BREAKPOINTS}
          cols={COLS}
          width={width as number}
          rowHeight={rowHeight}
          margin={MARGIN}
          containerPadding={[0, 0]}
          compactor={verticalCompactor}
          dragConfig={{ enabled: editing }}
          resizeConfig={{ enabled: editing, handles: ["se", "s", "e"] }}
          onLayoutChange={handleLayoutChange}
        >
          {tiles.map((tile) => (
            <div key={tile.id} className="puff-tile-shell relative">
              <div className="h-full min-h-0 [&>*:first-child]:h-full">
                {tile.node}
              </div>
              {editing && (
                <div
                  className="absolute inset-0 z-30 cursor-grab rounded-[28px] active:cursor-grabbing"
                  style={{
                    border:
                      "1px dashed color-mix(in srgb, var(--pink) 60%, transparent)",
                    background:
                      "color-mix(in srgb, var(--pink) 5%, transparent)",
                  }}
                >
                  {/* This overlay owns the pointer while arranging; the resize
                      grips are lifted above it (z 45) in css/_theme.css so a
                      drag on a corner still resizes instead of moving. */}
                  {editBar ? (
                    <div
                      className="absolute left-3 top-3 z-40 flex items-center gap-1.5"
                      onPointerDown={(e) => e.stopPropagation()}
                      onMouseDown={(e) => e.stopPropagation()}
                      onTouchStart={(e) => e.stopPropagation()}
                    >
                      {editBar(tile.id)}
                    </div>
                  ) : (
                  <span
                    className="absolute left-3 top-3 rounded-full border px-2 py-1"
                    style={{
                      fontFamily:
                        "var(--font-jbmono), 'JetBrains Mono', monospace",
                      fontWeight: 700,
                      fontSize: 9,
                      letterSpacing: "0.14em",
                      textTransform: "uppercase",
                      color: "var(--pink)",
                      background: "var(--card)",
                      borderColor: "var(--line)",
                    }}
                  >
                    drag · resize ↘
                  </span>
                  )}
                </div>
              )}
              {editing && onRemoveTile && (
                <button
                  type="button"
                  aria-label={`Remove ${tile.id} tile`}
                  onClick={() => onRemoveTile(tile.id)}
                  onPointerDown={(e) => e.stopPropagation()}
                  onMouseDown={(e) => e.stopPropagation()}
                  onTouchStart={(e) => e.stopPropagation()}
                  className="absolute right-3 top-3 z-40 grid h-9 w-9 cursor-pointer place-items-center rounded-full border-0"
                  style={{ background: "var(--magenta)", color: "#fff" }}
                >
                  <span className="msr" style={{ fontSize: 20 }}>
                    close
                  </span>
                </button>
              )}
            </div>
          ))}
        </Responsive>
      ) : (
        <div className="grid grid-cols-4 gap-3.5">
          {tiles.slice(0, 8).map((t) => (
            <div
              key={t.id}
              className="h-40 animate-pulse rounded-[28px]"
              style={{ background: "var(--card2)" }}
            />
          ))}
        </div>
      )}
    </div>
  );
}
