import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { TASK_VIEWS } from "../workspaceTypes";
import type { WorkspaceState } from "./useWorkspaceState";
import useConversationViewport from "./useConversationViewport";
import usePreviewLayout, { defaultPreviewWidth, previewWidthBounds, previewWidthFromRatio, previewWidthRatio } from "../usePreviewLayout";
import type { PreviewRequest } from "../FilePreviewWorkspace";
import type { ArtifactDiff } from "../artifactHistory";
import { nativeFileSource } from "../workspaceFiles";

export default function useWorkspacePreview(workspace: WorkspaceState & ReturnType<typeof useConversationViewport>) {
  const { native, view, sidebarCollapsed, setReadingPaused, scrollRef, followBottom, manualPause, readingAnchor, connectionKey, captureReadingPosition } = workspace;
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewExpanded, setPreviewExpanded] = useState(false);
  const previewResizeRestore = useRef<"tabs" | "closed" | null>(null);
  const previewResizeBreakpoints = useRef({ below960: false, below720: false, shellBelow720: false });
  const previewResizeState = useRef({ open: false, sidebarOpen: true });
  previewResizeState.current = { open: previewOpen, sidebarOpen: !sidebarCollapsed && !(previewOpen && previewExpanded) };
  const [viewportWidth, setViewportWidth] = useState(window.innerWidth);
  const [previewPaneSize, setPreviewPaneSize] = useState({ width: window.innerWidth - 260, height: window.innerHeight - 36 });
  const [preferredPreviewRatio, setPreferredPreviewRatio] = useState<number | null>(null);
  const initialPreviewWidth = defaultPreviewWidth(previewPaneSize.width);
  const previewBounds = previewWidthBounds(previewPaneSize.width);
  const previewWidthPixels = previewWidthFromRatio(preferredPreviewRatio ?? previewWidthRatio(initialPreviewWidth, previewPaneSize.width), previewPaneSize.width);
  const previewWidth = 100 * previewWidthPixels / Math.max(1, previewPaneSize.width);
  useEffect(() => {
    const resize = () => setViewportWidth(window.innerWidth);
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, []);
  const [previewRequest, setPreviewRequest] = useState<PreviewRequest | null>(null);
  const [workspaceChanges, setWorkspaceChanges] = useState<number>();
  const [historyStats, setHistoryStats] = useState<{ identity: string; files: Record<string, { added: number; removed: number }> }>({ identity: "", files: {} });
  const historyIdentity = useRef(connectionKey); historyIdentity.current = connectionKey;
  const fileSource = useMemo(() => native ? nativeFileSource() : null, [native, connectionKey]);
  const previewSerial = useRef(0);
  const openPreview = (mode: PreviewRequest["mode"], path?: string, paths?: string[], receiptId?: string) => {
    captureReadingPosition();
    previewResizeRestore.current = null;
    setPreviewOpen(true); setPreviewRequest({ id: ++previewSerial.current, mode, path, paths, receiptId });
  };
  const closePreview = () => { captureReadingPosition(); previewResizeRestore.current = null; setPreviewOpen(false); setWorkspaceChanges(undefined); requestAnimationFrame(() => document.querySelector<HTMLButtonElement>('.conversation-topbar :is(.home-open-files, .conversation-more-toggle)')?.focus({ preventScroll: true })); };
  const resizePreview = (width: number) => {
    captureReadingPosition();
    if (width < 160) { closePreview(); return; }
    if (width > previewBounds.maximum && previewPaneSize.width - width < 160) { setPreviewExpanded(true); return; }
    setPreferredPreviewRatio(previewWidthRatio(width, previewPaneSize.width));
  };
  const recordHistoryStats = (record: ArtifactDiff) => {
    if (historyIdentity.current !== connectionKey) return;
    setHistoryStats((old) => ({ identity: connectionKey, files: { ...(old.identity === connectionKey ? old.files : {}), [record.receipt_id]: { added: record.added, removed: record.removed } } }));
  };
  function revealRequest(messageId: string) {
    const node = Array.from(scrollRef.current?.querySelectorAll<HTMLElement>("[data-scroll-key]") || []).find((item) => item.dataset.scrollKey === `message:${messageId}`);
    if (!node) return false;
    manualPause.current = true; followBottom.current = false; setReadingPaused(true); previewResizeRestore.current = null; setPreviewOpen(false);
    window.setTimeout(() => { node.scrollIntoView({ block: "center", behavior: "instant" }); node.setAttribute("tabindex", "-1"); node.focus({ preventScroll: true }); }, window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 180);
    return true;
  }
  const { workspaceRef, previewPresent } = usePreviewLayout({ open: previewOpen, width: previewExpanded || viewportWidth <= 720 ? 100 : previewWidth, visible: view === "chat" || TASK_VIEWS.includes(view), scrollRef, followBottom, readingAnchor, captureReadingPosition });
  useLayoutEffect(() => {
    const element = workspaceRef.current;
    if (!element) return;
    const shell = element.closest<HTMLElement>(".app-shell");
    const observer = new ResizeObserver(() => {
      if (!element.clientWidth) return;
      const height = (shell?.clientHeight || window.innerHeight) - 36;
      setPreviewPaneSize((old) => old.width === element.clientWidth && old.height === height ? old : { width: element.clientWidth, height });
      // Root.H / gbrq.Kg / gbrq.g3: breakpoint crossings, not file-open rules.
      // Add only an occupied sidebar slot; narrow-window navigation is a fixed overlay.
      const rail = shell?.querySelector<HTMLElement>(".rail");
      const sidebarWidth = rail && getComputedStyle(rail).position !== "fixed" ? rail.getBoundingClientRect().width : 0;
      const workspaceWidth = element.clientWidth + sidebarWidth;
      const next = { below960: workspaceWidth <= 960, below720: workspaceWidth <= 720, shellBelow720: (shell?.clientWidth || window.innerWidth) <= 720 };
      const previous = previewResizeBreakpoints.current;
      previewResizeBreakpoints.current = next;
      if (next.below960 === previous.below960 && next.below720 === previous.below720 && next.shellBelow720 === previous.shellBelow720) return;
      const current = previewResizeState.current;
      if ((next.below960 !== previous.below960 && current.sidebarOpen && next.below960) || (next.below720 !== previous.below720 && next.below720)) {
        // Like Kg, a second narrower crossing replaces the saved open/closed state.
        previewResizeRestore.current = current.open ? "tabs" : "closed";
        if (current.open) { captureReadingPosition(); setPreviewOpen(false); }
      } else if (!next.below720 && !(current.sidebarOpen && next.below960)) {
        const restore = previewResizeRestore.current;
        previewResizeRestore.current = null;
        if (restore === "tabs") setPreviewOpen(true);
      }
    });
    observer.observe(element);
    if (shell) observer.observe(shell);
    return () => observer.disconnect();
  }, [workspaceRef]);
  return { previewOpen, setPreviewOpen, previewExpanded, setPreviewExpanded, previewResizeRestore, previewResizeBreakpoints, previewResizeState, viewportWidth, setViewportWidth, previewPaneSize, setPreviewPaneSize, preferredPreviewRatio, setPreferredPreviewRatio, initialPreviewWidth, previewBounds, previewWidthPixels, previewWidth, previewRequest, setPreviewRequest, workspaceChanges, setWorkspaceChanges, historyStats, setHistoryStats, historyIdentity, fileSource, previewSerial, openPreview, closePreview, resizePreview, recordHistoryStats, revealRequest, workspaceRef, previewPresent };
}


