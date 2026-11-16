import { useLayoutEffect, useRef, useState, type RefObject } from "react";

type ReadingAnchor = { key: string; offset: number; top: number };

// Direct ports of DUYS.i/a/s/o/Ju/Mv in 907538.0572f09d40.js @UTF16 2636339.
// Persistence is deliberately supplied by the caller's in-memory state.
export function previewWidthBounds(workspaceWidth: number) {
  const maximum = Math.max(320, workspaceWidth - 352);
  return { minimum: Math.min(320, maximum), maximum };
}
export function defaultPreviewWidth(workspaceWidth: number) {
  // The supplied desktop reference devotes 800 of 1704 workspace pixels to files.
  const { minimum, maximum } = previewWidthBounds(workspaceWidth);
  return Math.max(minimum, Math.min(maximum, Math.round(workspaceWidth * .47)));
}
export function previewWidthRatio(width: number, workspaceWidth: number) {
  const { minimum, maximum } = previewWidthBounds(workspaceWidth);
  const clamped = Math.max(minimum, Math.min(Number.isFinite(width) ? width : 600, maximum));
  return maximum === minimum ? 0 : Math.max(0, Math.min(1, (clamped - minimum) / (maximum - minimum)));
}
export function previewWidthFromRatio(ratio: number, workspaceWidth: number) {
  const { minimum, maximum } = previewWidthBounds(workspaceWidth);
  return minimum + Math.max(0, Math.min(1, ratio)) * (maximum - minimum);
}

// The same cubic-bezier(0,0,.2,1) used by disclosures, evaluated for measured layout.
function easeOut(progress: number) {
  let low = 0, high = 1;
  for (let step = 0; step < 12; step += 1) {
    const time = (low + high) / 2;
    if (.6 * time * time + .4 * time * time * time < progress) low = time;
    else high = time;
  }
  const time = (low + high) / 2;
  return 3 * time * time - 2 * time * time * time;
}

/** Retain the preview DOM through exit, and keep the same transcript anchor each frame. */
export default function usePreviewLayout({ open, width, visible, scrollRef, followBottom, readingAnchor, captureReadingPosition }: {
  open: boolean; width: number; visible: boolean; scrollRef: RefObject<HTMLDivElement>;
  followBottom: { current: boolean }; readingAnchor: { current: ReadingAnchor | null };
  captureReadingPosition: () => void;
}) {
  const workspaceRef = useRef<HTMLDivElement>(null);
  const [present, setPresent] = useState(false);
  const progress = useRef(0);
  const capture = useRef(captureReadingPosition);
  capture.current = captureReadingPosition;

  useLayoutEffect(() => {
    const workspace = workspaceRef.current;
    if (!workspace) return;
    workspace.querySelector(".file-preview-workspace")?.toggleAttribute("inert", !open);
    workspace.querySelector(".preview-divider")?.toggleAttribute("inert", !open || width >= 100);
    workspace.querySelector(".conversation-shell")?.toggleAttribute("inert", open && width >= 100);
    if (open) setPresent(true);
    if (!readingAnchor.current) capture.current();
    const anchor = readingAnchor.current;
    const apply = (value: number) => {
      progress.current = value;
      workspace.style.setProperty("--preview-current-width", `${width * value}%`);
      workspace.style.setProperty("--preview-divider-width", "0px");
      workspace.style.setProperty("--preview-progress", String(value));
      const timeline = scrollRef.current;
      if (!visible || !timeline?.getClientRects().length) return;
      if (followBottom.current) timeline.scrollTop = timeline.scrollHeight;
      else if (anchor) {
        const node = Array.from(timeline.querySelectorAll<HTMLElement>("[data-scroll-key]")).find((item) => item.dataset.scrollKey === anchor.key);
        timeline.scrollTop = node ? timeline.scrollTop + node.getBoundingClientRect().top - timeline.getBoundingClientRect().top - anchor.offset : anchor.top;
      }
    };
    const target = open ? 1 : 0, start = progress.current;
    if (start === target || !visible || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      apply(target); setPresent(open); return;
    }
    let frame = 0;
    const started = performance.now(), duration = 150 * Math.abs(target - start);
    const tick = (now: number) => {
      const elapsed = Math.max(0, Math.min(1, (now - started) / duration));
      apply(elapsed === 1 ? target : start + (target - start) * easeOut(elapsed));
      if (elapsed < 1) frame = requestAnimationFrame(tick);
      else setPresent(open);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
    // Polling and new callback identities must never replay a layout transition.
  }, [open, width, visible, scrollRef, followBottom, readingAnchor]);

  return { workspaceRef, previewPresent: present };
}
