import { useEffect, useRef, useState, type ReactNode } from "react";

export interface TitlebarProps {
  route: string; onNavigate: (route: string) => void;
  sidebarCollapsed: boolean; onToggleSidebar: () => void; connected: boolean;
  onSettings: () => void; onShortcuts: () => void; onFiles: () => void; onRefresh: () => void;
  health?: ReactNode;
}

/** Window navigation and application commands; history stays in memory. */
export default function Titlebar(props: TitlebarProps) {
  const [menu, setMenu] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [about, setAbout] = useState(false);
  const [history, setHistory] = useState({ routes: [props.route], index: 0 });
  const bar = useRef<HTMLElement>(null);
  const menuTrigger = useRef<HTMLButtonElement | null>(null);
  const target = useRef<HTMLInputElement | HTMLTextAreaElement | null>(null);
  const going = useRef(false);
  const lastRoute = useRef(props.route);
  const aboutClose = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (lastRoute.current === props.route) return;
    lastRoute.current = props.route;
    if (going.current) { going.current = false; return; }
    setHistory((current) => ({ routes: [...current.routes.slice(0, current.index + 1), props.route], index: current.index + 1 }));
  }, [props.route]);
  useEffect(() => {
    if (!menu) return;
    const close = (event: PointerEvent) => { if (event.target instanceof Node && !bar.current?.contains(event.target)) setMenu(null); };
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setMenu(null); menuTrigger.current?.focus(); }
      if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
      const items = Array.from(bar.current?.querySelectorAll<HTMLButtonElement>('.application-menu-panel [role="menuitem"]:not(:disabled)') || []);
      if (!items.length) return;
      event.preventDefault();
      const index = items.indexOf(document.activeElement as HTMLButtonElement);
      items[event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : event.key === "ArrowDown" ? (index + 1) % items.length : (index - 1 + items.length) % items.length].focus();
    };
    document.addEventListener("pointerdown", close); document.addEventListener("keydown", key);
    return () => { document.removeEventListener("pointerdown", close); document.removeEventListener("keydown", key); };
  }, [menu]);
  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(""), 4000);
    return () => window.clearTimeout(timer);
  }, [notice]);
  useEffect(() => { if (about) aboutClose.current?.focus(); }, [about]);
  const move = (offset: number) => {
    const index = history.index + offset;
    if (index < 0 || index >= history.routes.length) return;
    going.current = true; setHistory({ ...history, index }); props.onNavigate(history.routes[index]);
  };
  const edit = async (command: "copy" | "cut" | "paste" | "selectAll") => {
    const input = target.current; input?.focus();
    try {
      if (command === "paste" && input) {
        const value = await navigator.clipboard.readText();
        // Use the native setter so React receives the edit through its input tracker.
        const setter = Object.getOwnPropertyDescriptor(input instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, "value")?.set;
        const start = input.selectionStart || 0, end = input.selectionEnd || 0;
        setter?.call(input, input.value.slice(0, start) + value + input.value.slice(end));
        input.dispatchEvent(new Event("input", { bubbles: true })); input.setSelectionRange(start + value.length, start + value.length);
      } else if (!document.execCommand(command)) setNotice("请使用键盘快捷键完成此操作。");
    } catch { setNotice("请使用键盘快捷键完成此操作。"); }
  };
  const commands: Record<string, { label: string; action: () => void; shortcut?: string }[]> = {
    文件: [{ label: "长期对话", action: () => props.onNavigate("chat") }, { label: "打开工作空间文件", action: props.onFiles }, { label: "设置…", action: props.onSettings, shortcut: "Ctrl+," }],
    编辑: [{ label: "复制", action: () => void edit("copy"), shortcut: "Ctrl+C" }, { label: "剪切", action: () => void edit("cut"), shortcut: "Ctrl+X" }, { label: "粘贴", action: () => void edit("paste"), shortcut: "Ctrl+V" }, { label: "全选", action: () => void edit("selectAll"), shortcut: "Ctrl+A" }],
    视图: [{ label: props.sidebarCollapsed ? "显示侧边栏" : "隐藏侧边栏", action: props.onToggleSidebar }, { label: "运行活动", action: () => props.onNavigate("activity") }, { label: "记忆", action: () => props.onNavigate("memory") }, { label: "刷新当前视图", action: props.onRefresh }],
    帮助: [{ label: "键盘快捷键", action: props.onShortcuts }, { label: "关于 FORGE", action: () => setAbout(true) }],
  };
  return <>
    <header className="desktop-chrome titlebar" ref={bar} data-tauri-drag-region>
      <div className="titlebar-navigation">
        <button type="button" className="sidebar-toggle" aria-label="返回" title="返回" disabled={history.index === 0} onClick={() => move(-1)}><svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m11 5-5 5 5 5M6 10h10" /></svg></button>
        <button type="button" className="sidebar-toggle" aria-label="前进" title="前进" disabled={history.index === history.routes.length - 1} onClick={() => move(1)}><svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m9 5 5 5-5 5M4 10h10" /></svg></button>
        <button type="button" className="sidebar-toggle" aria-label={props.sidebarCollapsed ? "展开工作空间侧栏" : "收起工作空间侧栏"} aria-expanded={!props.sidebarCollapsed} title={props.sidebarCollapsed ? "显示侧边栏" : "隐藏侧边栏"} onClick={props.onToggleSidebar}><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3" /><path d="M9 4v16" /></svg></button>
      </div>
      <div role="menubar" aria-label="应用程序菜单" className="application-menubar">
        {Object.entries(commands).map(([name, entries]) => <div className="application-menu" key={name}>
          <button type="button" role="menuitem" aria-haspopup="menu" aria-expanded={menu === name} onPointerDown={() => { const element = document.activeElement; if (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement) target.current = element; }} onClick={(event) => { menuTrigger.current = event.currentTarget; setMenu(menu === name ? null : name); }} onKeyDown={(event) => { if (event.key === "ArrowDown") { event.preventDefault(); menuTrigger.current = event.currentTarget; setMenu(name); requestAnimationFrame(() => bar.current?.querySelector<HTMLButtonElement>('.application-menu-panel button')?.focus()); } }}>{name}</button>
          {menu === name && <div className="application-menu-panel" role="menu" aria-label={`${name}菜单`}>{entries.map((entry) => <button type="button" role="menuitem" key={entry.label} onClick={() => { setMenu(null); entry.action(); }}><span>{entry.label}</span>{entry.shortcut && <kbd>{entry.shortcut}</kbd>}</button>)}</div>}
        </div>)}
      </div>
      <div className="titlebar-actions"><span className="titlebar-brand">FORGE</span>{props.health ?? <span className="connection-indicator"><span className={props.connected ? "status-dot on" : "status-dot"} />{props.connected ? "Core 已连接" : "Core 未连接"}</span>}</div>
    </header>
    {notice && <div className="titlebar-notice" role="status">{notice}</div>}
    {about && <div className="modal-backdrop" onClick={() => { setAbout(false); menuTrigger.current?.focus(); }}><section className="app-modal" role="dialog" aria-modal="true" aria-label="关于 FORGE" onClick={(event) => event.stopPropagation()} onKeyDown={(event) => { if (event.key === "Escape") { setAbout(false); menuTrigger.current?.focus(); } if (event.key === "Tab") { event.preventDefault(); aboutClose.current?.focus(); } }}><h2>FORGE</h2><p>一个持续对话的工作空间。Router 协调后台子智能体，进展与交付回到同一条对话。</p><p className="notice">桌面前端 0.2.0</p><button ref={aboutClose} type="button" className="button" onClick={() => { setAbout(false); menuTrigger.current?.focus(); }}>关闭</button></section></div>}
  </>;
}
