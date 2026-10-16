import React, { ReactNode } from "react";

type Props = {
  zIndex?: string;
  type?: string;
  children?: ReactNode;
  className?: string;
  onClick?: (e: React.MouseEvent) => void;
};

export default function OverlayLayer({
  zIndex = "z-50",
  type = "flex",
  children,
  className,
  ...props
}: Props) {
  const handleClick = (e: React.MouseEvent) => {
    e.preventDefault();

    if (props.onClick) {
      props.onClick(e);
    }
  };

  return (
    <div
      className={`${type} ${zIndex} ${className} fixed inset-0 flex-col items-center justify-center overflow-hidden`}
    >
      <div
        className={`overlay absolute inset-0 animate-overlay-fade-in bg-linear-to-tr opacity-90 from-(--puff-surface-deep) via-(--puff-surface-deep) to-(--puff-surface-deep)`}
        onClick={handleClick}
      ></div>

      {children}
    </div>
  );
}
