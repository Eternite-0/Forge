import { useRef, useState, type KeyboardEvent, type ReactNode } from "react";

export type SettingId = "general" | "shortcuts" | "models" | "environment" |
  "storage" | "connections" | "maintenance" | "memory" | "triggers";

export interface SettingItem {
  id: SettingId;
  label: string;
  description: string;
  accessibleLabel?: string;
  keywords?: string;
}
interface SettingGroup { label: string; items: SettingItem[] }

export const settingGroups: SettingGroup[] = [
  { label: "个人", items: [
    { id: "general", label: "常规", accessibleLabel: "常规", description: "查看当前工作空间、Router 模型与 Core 连接状态。", keywords: "工作空间 概况 目录 路径 状态" },
    { id: "shortcuts", label: "键盘快捷键", description: "查找发送消息、换行和打开设置的键盘操作。" },
  ] },
  { label: "模型与运行", items: [
    { id: "models", label: "模型库", description: "管理模型档案、协议支持、上下文上限与能力来源。", keywords: "Registry 供应商 能力测量 启用 禁用" },
    { id: "environment", label: "运行配置", accessibleLabel: "环境", description: "配置模型连接、Router、Token 上限及网络访问范围。", keywords: "供应商 地址 API 网络 白名单 并发 重启" },
    { id: "storage", label: "用量与预算", accessibleLabel: "存储", description: "查看 Token 预算、已确认用量、未结算预留与并发占用。", keywords: "费用 货币 消耗 账本" },
  ] },
  { label: "安全与数据", items: [
    { id: "memory", label: "记忆", description: "检索和管理任务记忆、来源与有效期。", keywords: "recall 来源 决策 历史 撤销" },
    { id: "triggers", label: "触发器", description: "登记事件源，创建定时任务、文件监听或提交事件。", keywords: "定时 调度 文件 事件 source schedule watch" },
    { id: "connections", label: "供应商凭据", accessibleLabel: "连接", description: "在桌面应用的系统凭据库中保存、检查和删除供应商凭据。", keywords: "密钥 API key credential" },
    { id: "maintenance", label: "备份与恢复", description: "备份和恢复工作空间，检查维护状态与恢复后的写入锁定。", keywords: "维护 数据 回滚 Core 重启" },
  ] },
];

export const settingMetadata = Object.fromEntries(settingGroups.flatMap((group) => group.items).map((item) => [item.id, item])) as Record<SettingId, SettingItem>;

function SettingsIcon({ id }: { id: SettingId }) {
  const symbols: Record<SettingId, ReactNode> = {
    general: <><path d="m9.5 3-1 2H6L4.5 7.6l1.3 2.2v4.4l-1.3 2.2L6 19h2.5l1 2h5l1-2H18l1.5-2.6-1.3-2.2V9.8l1.3-2.2L18 5h-2.5l-1-2h-5Z" /><circle cx="12" cy="12" r="3.2" /></>,
    shortcuts: <><rect x="2.5" y="5.5" width="19" height="13" rx="2" /><path d="M6 9h1m4 0h1m4 0h1M6 12h1m4 0h1m4 0h1M7 15.5h10" /></>,
    models: <><path d="m12 2.5 9 5-9 5-9-5 9-5Z" /><path d="m3 12 9 5 9-5m-18 4.5 9 5 9-5" /></>,
    environment: <><rect x="3" y="4" width="18" height="14" rx="2" /><path d="M3 14h18m-12 7h6m-3-3v3" /></>,
    storage: <><path d="M3 7.5h7l2 2h9v9a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18.5v-11Z" /><path d="M3 7.5V5a1 1 0 0 1 1-1h5l3 3.5M3 10h18" /></>,
    connections: <><circle cx="8" cy="8" r="4.5" /><path d="m11.5 11.5 9 9m-3-3 2.5-2.5m-5.5-.5 2.5-2.5" /></>,
    memory: <><path d="M4 4h16v16H4zM8 8h8M8 12h8M8 16h5" /></>,
    triggers: <><circle cx="12" cy="12" r="8.5" /><path d="M12 6v6l4 2" /></>,
    maintenance: <><path d="M4 9a8 8 0 1 1 0 6M4 3v6h6" /><path d="M12 7v5l3 2" /></>,
  };
  return <svg className="settings-nav-icon" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">{symbols[id]}</svg>;
}

export function SettingsRow({ label, description, children }: { label: ReactNode; description?: ReactNode; children: ReactNode }) {
  return <div className="settings-row">
    <div className="settings-row-text"><div className="settings-row-label">{label}</div>{description && <div className="settings-row-desc">{description}</div>}</div>
    <div className="settings-row-control settings-row-value">{children}</div>
  </div>;
}

/** A shared section keeps every settings page on the same title and row grid. */
export function SettingsSection({ title, description, children, className = "" }: {
  title: ReactNode; description?: ReactNode; children: ReactNode; className?: string;
}) {
  return <section className={`settings-block ${className}`}>
    <h2 className="settings-block-heading">{title}</h2>
    {description && <p className="settings-block-description">{description}</p>}
    <div className="settings-group">{children}</div>
  </section>;
}

export default function SettingsView({ selected, onSelect, onClose, children }: { selected: SettingId; onSelect: (id: SettingId) => void; onClose: () => void; children: ReactNode }) {
  const [query, setQuery] = useState("");
  const searchInput = useRef<HTMLInputElement>(null);
  const itemButtons = useRef<Partial<Record<SettingId, HTMLButtonElement | null>>>({});
  const search = query.trim().toLocaleLowerCase();
  const groups = settingGroups.map((group) => ({ ...group, items: group.items.filter((item) => `${group.label} ${item.label} ${item.description} ${item.accessibleLabel || ""} ${item.keywords || ""}`.toLocaleLowerCase().includes(search)) })).filter((group) => group.items.length > 0);
  const visibleItems = groups.flatMap((group) => group.items);
  const current = settingMetadata[selected];
  const clearSearch = () => { setQuery(""); searchInput.current?.focus(); };
  const activate = (item: SettingItem) => { onSelect(item.id); itemButtons.current[item.id]?.focus(); };
  const navigate = (event: KeyboardEvent<HTMLButtonElement>, id: SettingId) => {
    if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229 || event.altKey || event.ctrlKey || event.metaKey) return;
    const index = visibleItems.findIndex((item) => item.id === id);
    const next = event.key === "ArrowDown" ? (index + 1) % visibleItems.length
      : event.key === "ArrowUp" ? (index - 1 + visibleItems.length) % visibleItems.length
      : event.key === "Home" ? 0 : event.key === "End" ? visibleItems.length - 1 : -1;
    if (next < 0) return;
    event.preventDefault();
    activate(visibleItems[next]);
  };
  return <section className="settings-view" aria-label="设置">
    <nav className="settings-nav" aria-label="设置导航">
      <div className="settings-nav-header-group">
        <div className="settings-nav-header"><h1 className="settings-page-title">设置</h1><button type="button" className="settings-back" aria-label="返回对话" title="返回对话" onClick={onClose}><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m10 5-7 7 7 7M3 12h18" /></svg></button></div><div className="settings-search"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 4 4" /></svg><input ref={searchInput} type="search" aria-label="搜索设置" placeholder="搜索设置" value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => {
        if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
        if (event.key === "Escape") { event.preventDefault(); clearSearch(); }
        else if (event.key === "Enter" && visibleItems.length) { event.preventDefault(); activate(visibleItems[0]); }
      }} />{query && <button type="button" className="settings-search-clear" aria-label="清除设置搜索" title="清除设置搜索" onClick={clearSearch}><svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true"><path d="m4 4 8 8m0-8-8 8" /></svg></button>}</div>
      </div>
      <div className="settings-nav-sections">
      {groups.map((group) => <div className="settings-nav-section" key={group.label}>
        <h2 className="settings-nav-group">{group.label}</h2>
        <div className="settings-nav-items">{group.items.map((item) => <button type="button" key={item.id} ref={(button) => { itemButtons.current[item.id] = button; }} className={`settings-nav-item${selected === item.id ? " active" : ""}`} aria-label={item.accessibleLabel} aria-current={selected === item.id ? "page" : undefined} onKeyDown={(event) => navigate(event, item.id)} onClick={() => onSelect(item.id)}><SettingsIcon id={item.id} /><span>{item.label}</span></button>)}</div>
      </div>)}
      {!groups.length && <div className="settings-search-empty"><p className="notice">未找到匹配的设置</p><button type="button" className="text-button" onClick={clearSearch}>显示全部设置</button></div>}
      </div>
    </nav>
    <div className="settings-content"><div className="settings-content-inner" data-setting={selected}><header className="settings-content-header"><h1 className="settings-title">{current.label}</h1><p className="sr-only">{current.description}</p></header>{children}</div></div>
  </section>;
}
