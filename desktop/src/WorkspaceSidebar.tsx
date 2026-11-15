import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { currentWorkspaceProject } from "./projectContext";

export interface WorkspaceSidebarProps {
  connected: boolean;
  workspaceId: string;
  workspaceRoot: string;
  active: boolean;
  navigation: ReactNode;
  children: ReactNode;
}

function FolderIcon() {
  return <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 7h7l2 2h9v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V7Zm0 0V5a1 1 0 0 1 1-1h5l3 3" /></svg>;
}

export default function WorkspaceSidebar({
  connected, workspaceId, workspaceRoot, active, navigation, children,
}: WorkspaceSidebarProps) {
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  const [projectExpanded, setProjectExpanded] = useState(true);
  const [compactViewport, setCompactViewport] = useState(() => window.matchMedia("(max-width: 760px)").matches);
  const workspaceDialogId = useId();
  const projectContentId = useId();
  const workspaceRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const currentRef = useRef<HTMLButtonElement>(null);
  const projectContent = useRef<HTMLDivElement>(null);
  const workspaceProject = currentWorkspaceProject(connected ? workspaceId : "", connected ? workspaceRoot : "");
  const workspacePath = connected ? workspaceProject.path || workspaceId : "";
  const workspaceName = workspaceProject.name;
  const projectCanCollapse = active && !compactViewport;

  useEffect(() => { if (!active) setWorkspaceOpen(false); }, [active]);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 760px)");
    const update = () => setCompactViewport(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  useLayoutEffect(() => {
    const element = projectContent.current;
    if (!element) return;
    element.toggleAttribute("inert", projectCanCollapse && !projectExpanded);
    const finish = () => { element.style.height = !projectCanCollapse || projectExpanded ? "auto" : "0px"; element.style.opacity = !projectCanCollapse || projectExpanded ? "1" : "0"; };
    if (!projectCanCollapse || window.matchMedia("(prefers-reduced-motion: reduce)").matches) { finish(); return; }
    element.style.height = `${element.getBoundingClientRect().height}px`;
    void element.offsetHeight;
    let timeout = 0;
    const frame = requestAnimationFrame(() => {
      element.style.height = projectExpanded ? `${element.scrollHeight}px` : "0px";
      element.style.opacity = projectExpanded ? "1" : "0";
      timeout = window.setTimeout(finish, 200);
    });
    const transitioned = (event: TransitionEvent) => { if (event.target === element && event.propertyName === "height") finish(); };
    element.addEventListener("transitionend", transitioned);
    return () => { cancelAnimationFrame(frame); window.clearTimeout(timeout); element.removeEventListener("transitionend", transitioned); };
  }, [projectCanCollapse, projectExpanded]);

  const closeWorkspace = useCallback(() => {
    setWorkspaceOpen(false);
    triggerRef.current?.focus();
  }, []);
  useLayoutEffect(() => {
    const element = dialogRef.current;
    if (!element) return;
    element.toggleAttribute("inert", !workspaceOpen);
    const finish = () => { element.style.height = workspaceOpen ? "auto" : "0px"; element.style.opacity = workspaceOpen ? "1" : "0"; };
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) { finish(); return; }
    element.style.height = `${element.getBoundingClientRect().height}px`;
    void element.offsetHeight;
    const frame = requestAnimationFrame(() => { element.style.height = workspaceOpen ? `${element.scrollHeight}px` : "0px"; element.style.opacity = workspaceOpen ? "1" : "0"; });
    const timer = window.setTimeout(finish, 180);
    return () => { cancelAnimationFrame(frame); window.clearTimeout(timer); };
  }, [workspaceOpen]);

  useEffect(() => {
    if (!workspaceOpen) return;
    (currentRef.current || dialogRef.current)?.focus();

    function handlePointerDown(event: PointerEvent) {
      if (event.target instanceof Node && !workspaceRef.current?.contains(event.target)) closeWorkspace();
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        closeWorkspace();
      }
    }
    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [workspaceOpen, closeWorkspace]);

  return <div className="sidebar-panel" data-workspace-panel="">
    <div className="workspace-selector-wrap" ref={workspaceRef}>
      <button
        ref={triggerRef}
        type="button"
        className={`workspace-selector${workspaceOpen ? " is-open" : ""}`}
        aria-label="选择工作空间"
        aria-haspopup="dialog"
        aria-expanded={workspaceOpen}
        aria-controls={workspaceDialogId}
        title={workspacePath || undefined}
        onClick={() => workspaceOpen ? closeWorkspace() : setWorkspaceOpen(true)}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            if (workspaceOpen) (currentRef.current || dialogRef.current)?.focus();
            else setWorkspaceOpen(true);
          }
        }}
      >
        <span className="workspace-selector-name">FORGE</span>
        <svg className="workspace-selector-chevron" viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m5 6 3 3 3-3" /></svg>
      </button>

      <div
        id={workspaceDialogId}
        ref={dialogRef}
        className="workspace-menu"
        role="dialog"
        aria-label="工作空间选择"
        aria-hidden={!workspaceOpen}
        tabIndex={-1}
        onKeyDown={(event) => {
          if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
            event.preventDefault();
            (currentRef.current || dialogRef.current)?.focus();
          }
          if (event.key === "Tab") closeWorkspace();
        }}
      >
        <p className="workspace-menu-label">当前工作空间</p>
        {connected ? <button
          ref={currentRef}
          type="button"
          className="workspace-menu-current"
          aria-label="使用当前工作空间"
          aria-current="true"
          onClick={closeWorkspace}
        >
          <FolderIcon />
          <span className="workspace-menu-copy"><strong>{workspaceName}</strong><span className="workspace-menu-path">{workspacePath || "工作空间路径暂不可用"}</span></span>
          <svg className="workspace-menu-check" viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m5 10 3.5 3.5L15 7" /></svg>
        </button> : <p className="workspace-menu-empty">连接后显示工作空间</p>}
      </div>
    </div>
    <div className="sidebar-scroll">
      <section className="sidebar-project-group" data-project-key={workspaceProject.key} data-project-source="workspace">
        <h2 className="sidebar-section-label">项目</h2>
        <button type="button" className="sidebar-project-toggle" aria-label={`${projectExpanded ? "收起" : "展开"}项目 ${workspaceName}`} aria-expanded={projectExpanded} aria-controls={projectContentId} title={workspacePath || undefined} onClick={() => setProjectExpanded((expanded) => !expanded)} onKeyDown={(event) => {
          if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229 || event.altKey || event.ctrlKey || event.metaKey) return;
          if (event.key === "ArrowLeft") { event.preventDefault(); setProjectExpanded(false); }
          if (event.key === "ArrowRight") { event.preventDefault(); setProjectExpanded(true); }
          if (event.key === "ArrowDown" && projectExpanded) { event.preventDefault(); projectContent.current?.querySelector<HTMLButtonElement>("button")?.focus(); }
        }}>
          <svg className="project-chevron" data-chevron viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m6 4 4 4-4 4" /></svg>
          <FolderIcon /><span>{workspaceName}</span>
        </button>
        <div ref={projectContent} id={projectContentId} className="sidebar-project-content" aria-hidden={projectCanCollapse && !projectExpanded}>{navigation}</div>
      </section>
    </div>
    <div className="sidebar-connection">{children}</div>
  </div>;
}
