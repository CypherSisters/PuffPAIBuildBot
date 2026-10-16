import React, { ReactNode } from "react";
import { containerMaxW } from "../../_lib/config";

type Props = {
  children?: ReactNode;
};

export default function FooterBar({ children }: Props) {
  const year = new Date().getFullYear();

  return (
    <footer className={`px-6 py-2 ${containerMaxW}`}>
      <div className="block items-center justify-between md:flex">
        <div className="mb-6 text-center md:mb-0 md:text-left">
          <b>
            &copy;{year},{` `}
            Cypher Sisters · PuffPAI
          </b>
          {` `}
          {children}
        </div>
        <div className="text-sm text-(--text2) md:py-2">
          Privacy-first AI for the edge
        </div>
      </div>
    </footer>
  );
}
