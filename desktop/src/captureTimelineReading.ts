export interface TimelineReadingAnchor { key: string; offset: number; top: number }

/** Capture before replacing an independently polled activity snapshot. */
export function captureTimelineReading(element: HTMLDivElement | null, following: boolean): TimelineReadingAnchor | null {
  if (!element?.getClientRects().length || following) return null;
  const viewport = element.getBoundingClientRect();
  const candidates = Array.from(element.querySelectorAll<HTMLElement>("[data-scroll-key]"))
    .map((node) => ({ node, bounds: node.getBoundingClientRect() }))
    .filter(({ bounds }) => bounds.height > 0 && bounds.bottom > viewport.top && bounds.top < viewport.bottom);
  const preferred = candidates.filter(({ node }) => !/^group(?:-files)?:/.test(node.dataset.scrollKey || ""));
  const anchor = (preferred.length ? preferred : candidates).sort((left, right) => Math.abs(left.bounds.top - viewport.top) - Math.abs(right.bounds.top - viewport.top))[0];
  return anchor ? { key: anchor.node.dataset.scrollKey!, offset: anchor.bounds.top - viewport.top, top: element.scrollTop } : null;
}
