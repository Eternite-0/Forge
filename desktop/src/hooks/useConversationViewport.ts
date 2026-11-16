import { useCallback, useEffect, useLayoutEffect } from "react";
import type { WorkspaceState } from "./useWorkspaceState";

export default function useConversationViewport(workspace: WorkspaceState) {
  const { view, sidebarCollapsed, draft, scrollRef, composerInput, composerResizeFrame, followBottom, readingAnchor } = workspace;
  function captureReadingPosition() {
    const element = scrollRef.current;
    if (!element?.getClientRects().length || followBottom.current) return;
    const viewport = element.getBoundingClientRect();
    const candidates = Array.from(element.querySelectorAll<HTMLElement>("[data-scroll-key]"))
      .map((node) => ({ node, bounds: node.getBoundingClientRect() }))
      .filter(({ bounds }) => bounds.height > 0 && bounds.bottom > viewport.top && bounds.top < viewport.bottom);
    const preferred = candidates.filter(({ node }) => !/^group(?:-files)?:/.test(node.dataset.scrollKey || ""));
    const anchor = (preferred.length ? preferred : candidates).sort((a, b) => Math.abs(a.bounds.top - viewport.top) - Math.abs(b.bounds.top - viewport.top))[0];
    if (anchor) readingAnchor.current = { key: anchor.node.dataset.scrollKey!, offset: anchor.bounds.top - viewport.top, top: element.scrollTop };
  }
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
  return { captureReadingPosition, resizeComposer };
}

