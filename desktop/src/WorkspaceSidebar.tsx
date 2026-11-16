import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { currentWorkspaceProject } from "./projectContext";
import { request, type Connection } from "./api";
import { errorMessage } from "./logic";
import SidebarGroup from "./components/Sidebar/SidebarGroup";

export interface WorkspaceSidebarProps {
  connected: boolean;
  workspaceId: string;
  workspaceRoot: string;
  active: boolean;
  navigation: ReactNode;
  children?: ReactNode;
  topNavigation?: ReactNode;
  footer?: ReactNode;
  connection?: Connection | null;
}

function FolderIcon() {
  return <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 7h7l2 2h9v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V7Zm0 0V5a1 1 0 0 1 1-1h5l3 3" /></svg>;
}

export default function WorkspaceSidebar({
  connected, workspaceId, workspaceRoot, active, navigation, children, topNavigation, footer, connection,
}: WorkspaceSidebarProps) {
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [projectQuery, setProjectQuery] = useState("");
  const searchInput = useRef<HTMLInputElement>(null);
  const searchTrigger = useRef<HTMLButtonElement>(null);
  const projectSearchId = useId();
  const [discovery, setDiscovery] = useState<{ key: string; items: { id: string; root: string; name: string }[]; error: string }>({ key: "", items: [], error: "" });
  const identity = connection ? `${connection.base_url}|${connection.workspace_id || ""}|${connection.generation}` : "";
  useEffect(() => {
    if (!connection) return;
    const controller = new AbortController();
    request<{ id: string; root: string; name: string }[]>(connection, "/api/projects/discovered", { signal: controller.signal })
      .then((items) => { if (!controller.signal.aborted) setDiscovery({ key: identity, items: Array.isArray(items) ? items : [], error: "" }); })
      .catch((error) => { if (!controller.signal.aborted) setDiscovery({ key: identity, items: [], error: errorMessage(error) }); });
    return () => controller.abort();
  }, [identity, connection?.bearer]);
  const workspaceDialogId = useId();
  const workspaceRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const currentRef = useRef<HTMLButtonElement>(null);
  const workspaceProject = currentWorkspaceProject(connected ? workspaceId : "", connected ? workspaceRoot : "");
  const workspacePath = connected ? workspaceProject.path || workspaceId : "";
  const workspaceName = workspaceProject.name;
  const query = projectQuery.trim().toLocaleLowerCase();
  const matchesProject = (name: string, path: string) => !query || `${name} ${path}`.toLocaleLowerCase().includes(query);
  const discoveredProjects = discovery.key === identity ? discovery.items.filter((item) => item.root.replace(/\\/g, "/").toLowerCase() !== workspaceRoot.replace(/\\/g, "/").toLowerCase() && matchesProject(item.name, item.root)) : [];
  const currentMatches = matchesProject(workspaceName, workspacePath);
  useEffect(() => { if (searchOpen) searchInput.current?.focus(); }, [searchOpen]);

  useEffect(() => { if (!active) setWorkspaceOpen(false); }, [active]);

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
        aria-label="查看当前工作空间"
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
      <button ref={searchTrigger} type="button" className="sidebar-search-toggle" aria-label="筛选已加载项目" title="筛选已加载项目" aria-expanded={searchOpen} aria-controls={projectSearchId} onClick={() => { setSearchOpen(!searchOpen); if (searchOpen) setProjectQuery(""); }}><svg viewBox="0 0 20 20" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true"><circle cx="8.5" cy="8.5" r="5" /><path d="m12.5 12.5 4 4" /></svg></button>

      <div
        id={workspaceDialogId}
        ref={dialogRef}
        className="workspace-menu"
        role="dialog"
        aria-label="当前工作空间"
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
    {searchOpen && <div className="sidebar-project-search" id={projectSearchId}><input ref={searchInput} type="search" value={projectQuery} aria-label="按项目名称或路径筛选" placeholder="筛选项目…" onChange={(event) => setProjectQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); setSearchOpen(false); setProjectQuery(""); searchTrigger.current?.focus(); } }} /><span>仅筛选已加载项目</span></div>}
    <div className="sidebar-primary"><h2 className="sidebar-section-label">对话</h2>{navigation}</div>
    <div className="sidebar-scroll">
      {topNavigation && <div className="sidebar-tools"><h2 className="sidebar-section-label">工作记录</h2>{topNavigation}</div>}
      {connected && <section className="sidebar-project-summary" aria-label="项目上下文" data-project-key={workspaceProject.key} data-project-source="workspace">
        <SidebarGroup label="项目">
        {currentMatches && <details className="codex-project-record current-project"><summary title={workspacePath}><FolderIcon /><span>{workspaceName}</span><small>当前</small></summary><div className="codex-project-path">{workspacePath || "路径暂不可用"}<p>当前工作空间；在同一条对话中继续。</p></div></details>}
        {discoveredProjects.map((item) => <details className="codex-project-record" key={item.id}><summary title={item.root}><FolderIcon /><span>{item.name}</span><small>已识别</small></summary><div className="codex-project-path">{item.root}<p>只读项目记录，不切换工作空间或授予目录访问权限。</p></div></details>)}
        {query && !currentMatches && !discoveredProjects.length && <p className="sidebar-empty" role="status">没有匹配的已加载项目</p>}
        {discovery.key === identity && discovery.error && <p className="sidebar-empty" title={discovery.error}>项目发现暂不可用</p>}
        </SidebarGroup>
      </section>}
    </div>
    <div className="sidebar-connection">{children}</div>
    <div className="sidebar-footer">{footer}</div>
  </div>;
}
