import type { ReactNode } from "react";

/**
 * A deliberate, honest zero -- not a broken component. Used anywhere the API
 * has genuinely returned 0 for a figure this window, so it never reads as a
 * loading glitch or a missing-data bug.
 */
export function ZeroBanner({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="zero-banner" role="note">
      <span className="zero-icon" aria-hidden="true">
        ◇
      </span>
      <div>
        <h3>{title}</h3>
        <p>{children}</p>
      </div>
    </div>
  );
}
