import type { ColorButtonKey } from "../_interfaces";

export const colorsBgLight = {
  white: "bg-(--card) text-(--text)",
  light: "bg-(--puff-surface-deep) text-(--puff-text-strong)",
  contrast: "bg-(--card) text-(--text)",
  success: "bg-(--mint) border-(--mint) text-(--puff-on-accent)",
  danger: "bg-(--puff-red) border-(--puff-red) text-(--puff-on-accent)",
  warning: "bg-(--lemon) border-(--lemon) text-(--puff-on-accent)",
  info: "bg-(--sky) border-(--sky) text-(--puff-on-accent)",
};

export const colorsText = {
  white: "text-(--text)",
  light: "text-(--text2)",
  contrast: "text-(--puff-text-strong)",
  success: "text-(--mint)",
  danger: "text-(--puff-red)",
  warning: "text-(--lemon)",
  info: "text-(--sky)",
};

export const colorsOutline = {
  white: [colorsText.white, "border-(--line)"].join(" "),
  light: [colorsText.light, "border-(--line)"].join(" "),
  contrast: [colorsText.contrast, "border-(--line)"].join(" "),
  success: [colorsText.success, "border-(--mint)"].join(" "),
  danger: [colorsText.danger, "border-(--puff-red)"].join(" "),
  warning: [colorsText.warning, "border-(--lemon)"].join(" "),
  info: [colorsText.info, "border-(--sky)"].join(" "),
};

export const getButtonColor = (
  color: ColorButtonKey,
  isOutlined: boolean,
  hasHover: boolean,
  isActive = false,
) => {
  if (color === "void") {
    return "";
  }

  const colors = {
    ring: {
      white: "ring-(--line)",
      whiteDark: "ring-(--line)",
      lightDark: "ring-(--line)",
      contrast: "ring-(--line)",
      success: "ring-(--mint)",
      danger: "ring-(--puff-red)",
      warning: "ring-(--lemon)",
      info: "ring-(--sky)",
    },
    active: {
      white: "bg-(--card)",
      whiteDark: "bg-(--puff-surface-deep)",
      lightDark: "bg-(--chip)",
      contrast: "bg-(--card)",
      success: "bg-(--mint)",
      danger: "bg-(--puff-red)",
      warning: "bg-(--lemon)",
      info: "bg-(--sky)",
    },
    bg: {
      white: "bg-(--card) text-(--text)",
      whiteDark: "bg-(--puff-surface-deep) text-(--puff-text-strong)",
      lightDark: "bg-(--puff-surface-deep) text-(--puff-text-strong)",
      contrast: "bg-(--card) text-(--text)",
      success: "bg-(--mint) text-(--puff-on-accent)",
      danger: "bg-(--puff-red) text-(--puff-on-accent)",
      warning: "bg-(--lemon) text-(--puff-on-accent)",
      info: "bg-(--sky) text-(--puff-on-accent)",
    },
    bgHover: {
      white: "hover:bg-(--puff-card-hover)",
      whiteDark: "hover:bg-(--puff-card-hover)",
      lightDark: "hover:bg-(--puff-card-hover)",
      contrast: "hover:bg-(--puff-card-hover)",
      success:
        "hover:bg-(--mint) hover:border-(--mint)",
      danger:
        "hover:bg-(--puff-red) hover:border-(--puff-red)",
      warning:
        "hover:bg-(--lemon) hover:border-(--lemon)",
      info: "hover:bg-(--sky) hover:border-(--sky)",
    },
    borders: {
      white: "border-(--line)",
      whiteDark: "border-(--line)",
      lightDark: "border-(--line)",
      contrast: "border-(--line)",
      success: "border-(--mint)",
      danger: "border-(--puff-red)",
      warning: "border-(--lemon)",
      info: "border-(--sky)",
    },
    text: {
      contrast: "text-(--text)",
      success: "text-(--mint)",
      danger: "text-(--puff-red)",
      warning: "text-(--lemon)",
      info: "text-(--sky)",
    },
    outlineHover: {
      contrast:
        "hover:bg-(--puff-card-hover) hover:text-(--text)",
      success:
        "hover:bg-(--mint) hover:text-(--puff-on-accent) hover:border-(--mint)",
      danger:
        "hover:bg-(--puff-red) hover:text-(--puff-on-accent) hover:border-(--puff-red)",
      warning:
        "hover:bg-(--lemon) hover:text-(--puff-on-accent) hover:border-(--lemon)",
      info: "hover:bg-(--sky) hover:text-(--puff-on-accent) hover:border-(--sky)",
    },
  };

  const isOutlinedProcessed = isOutlined && ["white", "whiteDark", "lightDark"].indexOf(color) < 0;

  const base = [colors.borders[color], colors.ring[color]];

  if (isActive) {
    base.push(colors.active[color]);
  } else {
    base.push(isOutlinedProcessed ? colors.text[color] : colors.bg[color]);
  }

  if (hasHover) {
    base.push(isOutlinedProcessed ? colors.outlineHover[color] : colors.bgHover[color]);
  }

  return base.join(" ");
};
