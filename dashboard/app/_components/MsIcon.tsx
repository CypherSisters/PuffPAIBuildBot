import type { CSSProperties } from "react";

/**
 * Material Symbols Rounded ligature icon (self-hosted variable font,
 * @font-face + .msr class in css/_theme.css).
 *
 * Usage: <MsIcon name="mic" size={24} />  → <span class="msr">mic</span>
 * `fill` switches the variable font's FILL axis to the solid glyph.
 */
export default function MsIcon({
  name,
  size = 24,
  className,
  style,
  fill = false,
}: {
  name: string;
  size?: number;
  className?: string;
  style?: CSSProperties;
  fill?: boolean;
}) {
  return (
    <span
      className={className ? `msr ${className}` : "msr"}
      aria-hidden="true"
      style={{
        fontSize: size,
        fontVariationSettings: fill
          ? "'FILL' 1, 'wght' 400, 'GRAD' 0, 'opsz' 24"
          : undefined,
        ...style,
      }}
    >
      {name}
    </span>
  );
}
