/**
 * A link that opens a browser app's own page as a whole new page.
 *
 * The page `/dashboard/apps/<id>` may frame that one app's address only
 * because the middleware names it in the page's Content-Security-Policy
 * (`webAppFrameSrc`), and a policy is fixed when a page is loaded. A
 * client-side move (next/link, router.push) keeps the policy of the page it
 * came from, which names no app, so the browser shows "This content is
 * blocked". So every way into an app's page is a full page load; no page's
 * policy ever names more than its own app.
 *
 * Copyright (c) Cypher Sisters. Apache-2.0 License.
 */
import React from "react";
import Link from "next/link";

/** The issued shape of a browser app's page (the same id rule as the middleware). */
export const WEB_APP_PATH = /^\/dashboard\/apps\/[a-z0-9][a-z0-9-]{0,29}-[a-z2-7]{8}\/?$/;

export function isWebAppPath(href: unknown): boolean {
  return typeof href === "string" && WEB_APP_PATH.test(href.split(/[?#]/)[0]);
}

type Props = Omit<React.AnchorHTMLAttributes<HTMLAnchorElement>, "href"> & { href: string };

export default function AppLink({ href, children, ...rest }: Props) {
  if (isWebAppPath(href)) {
    return (
      <a href={href} {...rest}>
        {children}
      </a>
    );
  }
  return (
    <Link href={href} {...rest}>
      {children}
    </Link>
  );
}
