import { useCallback, useEffect, useLayoutEffect, useRef } from "react";
import { captureTimelineReading, type TimelineReadingAnchor } from "../captureTimelineReading";
import type { WorkspaceState } from "./useWorkspaceState";

export default function useConversationViewport(workspace: WorkspaceState) {
  const { view, sidebarCollapsed, draft, scrollRef, composerInput, composerResizeFrame, followBottom, readingAnchor } = workspace;
  // Keep a pre-reflow snapshot separate from the anchor consumed by polling.
  const windowReading = useRef({ width: window.innerWidth, height: window.innerHeight, anchor: null as TimelineReadingAnchor | null });
  const responsiveReading = useRef<{ anchor: TimelineReadingAnchor; frame: number; timer: number } | null>(null);
  const pendingWindowResize = () => windowReading.current.width !== window.innerWidth || windowReading.current.height !== window.innerHeight;
  function rememberTimelineReading() {
    if (pendingWindowResize() || responsiveReading.current) return;
    windowReading.current.anchor = captureTimelineReading(scrollRef.current, followBottom.current);
  }
  function captureReadingPosition() {
    const anchor = responsiveReading.current?.anchor ?? (pendingWindowResize() ? windowReading.current.anchor : captureTimelineReading(scrollRef.current, followBottom.current));
    if (anchor) readingAnchor.current = anchor;
  }
  function cancelResponsiveReading() {
    const pending = responsiveReading.current;
    if (!pending) return;
    cancelAnimationFrame(pending.frame);
    window.clearTimeout(pending.timer);
    responsiveReading.current = null;
    if (readingAnchor.current === pending.anchor) readingAnchor.current = null;
  }
  useEffect(() => {
    const timeline = scrollRef.current;
    if (!timeline) return;
    const apply = (anchor: TimelineReadingAnchor) => {
      if (!timeline.clientHeight) return;
      const node = Array.from(timeline.querySelectorAll<HTMLElement>("[data-scroll-key]")).find(element => element.dataset.scrollKey === anchor.key);
      if (node) timeline.scrollTop += node.getBoundingClientRect().top - timeline.getBoundingClientRect().top - anchor.offset;
    };
    const resize = () => {
      if (!pendingWindowResize()) return;
      const anchor = responsiveReading.current?.anchor ?? windowReading.current.anchor;
      cancelResponsiveReading();
      windowReading.current = { width: window.innerWidth, height: window.innerHeight, anchor };
      if (!anchor || followBottom.current) return;
      readingAnchor.current = anchor;
      // Restore before paint, and again after the responsive file panel settles.
      const frame = requestAnimationFrame(() => apply(anchor));
      const timer = window.setTimeout(() => {
        apply(anchor);
        cancelResponsiveReading();
        rememberTimelineReading();
      }, 220);
      responsiveReading.current = { anchor, frame, timer };
    };
    const onKey = (event: KeyboardEvent) => {
      if (!(event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) &&
          ["ArrowDown", "ArrowUp", "PageUp", "PageDown", "Home", "End", " "].includes(event.key)) cancelResponsiveReading();
    };
    window.addEventListener("resize", resize);
    timeline.addEventListener("wheel", cancelResponsiveReading, { passive: true });
    timeline.addEventListener("pointerdown", cancelResponsiveReading);
    timeline.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("resize", resize);
      timeline.removeEventListener("wheel", cancelResponsiveReading);
      timeline.removeEventListener("pointerdown", cancelResponsiveReading);
      timeline.removeEventListener("keydown", onKey);
      cancelResponsiveReading();
    };
  }, []);
  const resizeComposer = useCallback(() => {
    const input = composerInput.current;
    if (!input || !input.getClientRects().length) return;
    const timeline = scrollRef.current;
    const position = timeline?.scrollTop || 0;
    const previous = input.getBoundingClientRect().height;
    const minHeight = parseFloat(getComputedStyle(input).getPropertyValue("--composer-measured-min-height")) || 40;
    window.cancelAnimationFrame(composerResizeFrame.current);
    input.style.transition = "none";
    input.style.height = "0px";
    const natural = input.scrollHeight;
    const target = Math.max(minHeight, Math.min(180, natural));
    input.style.overflowY = natural > 180 ? "auto" : "hidden";
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches || Math.abs(previous - target) < .5) input.style.height = `${target}px`;
    else {
      input.style.height = `${previous}px`;
      void input.offsetHeight;
      composerResizeFrame.current = window.requestAnimationFrame(() => {
        input.style.transition = "height .15s cubic-bezier(0,0,.2,1)";
        input.style.height = `${target}px`;
      });
    }
    const footer = input.closest<HTMLElement>(".composer-wrap");
    input.closest<HTMLElement>(".conversation-shell")?.style.setProperty("--composer-bottom-offset", `${(footer?.offsetHeight || 160) + 12}px`);
    if (timeline) timeline.scrollTop = followBottom.current ? timeline.scrollHeight : position;
  }, []);
  useLayoutEffect(() => { resizeComposer(); }, [draft, view, sidebarCollapsed, resizeComposer]);
  useEffect(() => () => window.cancelAnimationFrame(composerResizeFrame.current), []);
  useEffect(() => {
    const footer = composerInput.current?.closest<HTMLElement>(".composer-wrap");
    if (!footer) return;
    const observer = new ResizeObserver(() => {
      const shell = footer.closest<HTMLElement>(".conversation-shell");
      shell?.style.setProperty("--conversation-footer-height", `${footer.offsetHeight}px`);
      shell?.style.setProperty("--composer-bottom-offset", `${footer.offsetHeight + 12}px`);
      if (followBottom.current && scrollRef.current?.getClientRects().length) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    });
    observer.observe(footer);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    const input = composerInput.current;
    if (!input) return;
    let width = -1;
    const observer = new ResizeObserver(([entry]) => {
      if (Math.abs(entry.contentRect.width - width) < .5) return;
      width = entry.contentRect.width;
      resizeComposer();
    });
    observer.observe(input);
    return () => observer.disconnect();
  }, [resizeComposer]);
  useEffect(() => {
    const element = scrollRef.current;
    if (!element) return;
    let height = element.clientHeight;
    const observer = new ResizeObserver(() => {
      if (element.clientHeight === height) return;
      height = element.clientHeight;
      if (followBottom.current) element.scrollTop = element.scrollHeight;
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return { captureReadingPosition, resizeComposer, rememberTimelineReading, cancelResponsiveReading, isRestoringResponsiveReading: () => pendingWindowResize() || !!responsiveReading.current };
}

