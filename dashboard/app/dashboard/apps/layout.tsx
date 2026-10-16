import { ReactNode } from "react";

type Props = {
  children: ReactNode;
};

export default function AppsLayout({ children }: Props) {
  return <>{children}</>;
}
