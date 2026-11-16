import { useId, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { isClosedQuest, namesLabel, needsQuestAttention, questDisplayTitle, statusLabel } from "./presentation";
import { explicitProjectName, projectFor, type ProjectFields } from "./projectContext";
import ShellIcon from "./components/ShellIcon";

type Row = Record<string, unknown>;

export interface WorkspaceQuest extends ProjectFields {
  id: string;
  title: string;
  status: string;
  version: number;
  goal: string;
}

export interface QuestWorkspaceProps {
  quests: WorkspaceQuest[];
  selected: WorkspaceQuest | null;
  selectedId: string;
  connected: boolean;
  loading?: boolean;
  detailRequest?: { id: string } | null;
  error: string;
  hasMore: boolean;
  onSelect: (id: string) => void;
  onLoadMore: () => void;
  plan: Row | null;
  planNotice: ReactNode;
  blocked: boolean;
  onApprove: () => void;
  grants: ReactNode;
  workspaceId?: string;
  workspaceRoot?: string;
  workspaceName?: string;
}

const text = (value: unknown, fallback = "未知"): string => value == null || value === "" ? fallback : String(value);
const records = (value: unknown): Row[] => Array.isArray(value)
  ? value.filter((item): item is Row => !!item && typeof item === "object" && !Array.isArray(item)) : [];
type QuestFilter = "all" | "active" | "attention" | "closed";
const QUEST_FILTERS: { id: QuestFilter; label: string }[] = [
  { id: "all", label: "全部" }, { id: "active", label: "未结束" },
  { id: "attention", label: "需处理" }, { id: "closed", label: "已结束" },
];

const QUEST_PHASES: Record<string, string> = {
  proposed: "已收到任务目标。",
  awaiting_approval: "需要你确认后才能继续。请先查看计划和权限。",
  approved: "已批准，等待执行。",
  queued: "正在排队等待执行。",
  running: "正在处理。",
  blocked: "暂时无法继续，请查看任务操作和运行记录。",
  paused: "任务已暂停。",
  needs_input: "需要更多信息，请回到对话补充说明。",
  reviewing: "正在检查结果。",
  completed: "任务已完成。",
  failed: "这次任务没有完成，可以在对话中讨论下一步。",
  cancelled: "任务已取消。",
  rejected: "任务未获批准。",
};

function costLimit(plan: Row): string {
  if (plan.max_cost_microunits === null) return "未设置";
  if (plan.max_cost_microunits === undefined) return "未知";
  return `${text(plan.max_cost_microunits)} 微单位 · ${text(plan.cost_currency, "币种未知")}`;
}

function criteria(value: unknown): string {
  return Array.isArray(value) ? value.map((item) => text(item)).join("；") || "未提供" : text(value, "未提供");
}

interface QuestProject { key: string; name: string; path: string; source: "workspace" | "metadata"; quests: WorkspaceQuest[]; loaded: number }

function QuestGroupDisclosure({ open, id, children }: { open: boolean; id: string; children: ReactNode }) {
  const panel = useRef<HTMLDivElement>(null);
  const initial = useRef(open);
  const previous = useRef(open);
  useLayoutEffect(() => {
    const element = panel.current;
    if (!element) return;
    element.toggleAttribute("inert", !open);
    if (previous.current === open) return;
    previous.current = open;
    const finish = () => { element.style.height = open ? "auto" : "0px"; element.style.opacity = open ? "1" : "0"; };
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) { finish(); return; }
    element.style.height = `${element.getBoundingClientRect().height}px`;
    void element.offsetHeight;
    let timeout = 0;
    const frame = requestAnimationFrame(() => {
      element.style.height = open ? `${element.scrollHeight}px` : "0px";
      element.style.opacity = open ? "1" : "0";
      timeout = window.setTimeout(finish, 200);
    });
    const transitioned = (event: TransitionEvent) => { if (event.target === element && event.propertyName === "height") finish(); };
    element.addEventListener("transitionend", transitioned);
    return () => { cancelAnimationFrame(frame); window.clearTimeout(timeout); element.removeEventListener("transitionend", transitioned); };
  }, [open]);
  return <div ref={panel} id={id} className="quest-group-content" aria-hidden={!open} style={{ height: initial.current ? "auto" : "0px", opacity: initial.current ? 1 : 0 }}>{children}</div>;
}

function QuestProjectGroup({ project, selectedId, search, filter, showHeading, onSelect }: { project: QuestProject; selectedId: string; search: string; filter: QuestFilter; showHeading: boolean; onSelect: (id: string, trigger: HTMLButtonElement) => void }) {
  const [expanded, setExpanded] = useState(true);
  const selectedClosed = project.quests.some((quest) => quest.id === selectedId && isClosedQuest(quest.status));
  const [closedExpanded, setClosedExpanded] = useState(selectedClosed || filter === "closed" || !!search);
  const contentId = useId(), closedId = useId();
  useLayoutEffect(() => {
    if (project.quests.some((quest) => quest.id === selectedId)) setExpanded(true);
    if (selectedClosed) setClosedExpanded(true);
  }, [selectedId, selectedClosed]);
  useLayoutEffect(() => {
    if (search || filter === "closed") { setExpanded(true); setClosedExpanded(true); }
  }, [search, filter]);
  const priority = (quest: WorkspaceQuest) => needsQuestAttention(quest.status) ? 0 : quest.status === "running" ? 1 : 2;
  const active = project.quests.filter((quest) => !isClosedQuest(quest.status)).sort((left, right) => priority(left) - priority(right));
  const closed = project.quests.filter((quest) => isClosedQuest(quest.status));
  const foldHistory = project.loaded > 5 && active.length > 0 && filter !== "closed" && !search;
  const projectLabel = project.key === `project:${project.name}` || project.key === `workspace:${project.name}` ? "未命名项目" : project.name;
  const row = (quest: WorkspaceQuest) => <button type="button" key={quest.id} className={selectedId === quest.id ? "list-row selected" : "list-row"}
    data-task-id={quest.id} aria-label={`${questDisplayTitle(quest.title, quest.id, quest.goal)}，${statusLabel(quest.status)}`} aria-current={selectedId === quest.id ? "page" : undefined} onClick={event => onSelect(quest.id, event.currentTarget)}>
    <span className="quest-row-label"><ShellIcon name={quest.status === "completed" ? "check" : needsQuestAttention(quest.status) || isClosedQuest(quest.status) ? "alert" : "clock"} size={16} /><strong>{questDisplayTitle(quest.title, quest.id, quest.goal)}</strong></span>
    <span className="state-label quest-state" data-state={quest.status}>{statusLabel(quest.status)}</span><ShellIcon name="chevronRight" size={14} className="quest-row-chevron" />
  </button>;
  return <section className="quest-project-group" data-project-key={project.key} data-project-source={project.source}>
    {showHeading && <button type="button" className="quest-project-toggle" aria-expanded={expanded} aria-controls={contentId} title={project.path || undefined} onClick={() => setExpanded((value) => !value)} onKeyDown={(event) => {
      if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229 || event.altKey || event.ctrlKey || event.metaKey) return;
      if (event.key === "ArrowRight") { event.preventDefault(); setExpanded(true); }
      if (event.key === "ArrowLeft") { event.preventDefault(); setExpanded(false); }
    }}><svg data-chevron viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m6 4 4 4-4 4" /></svg><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 7h7l2 2h9v12H3V7Zm0 0V4h7l3 3" /></svg><span>{projectLabel}</span><small title="当前已加载的任务数量">{project.loaded}</small></button>}
    <QuestGroupDisclosure open={!showHeading || expanded} id={contentId}>
      <div className="quest-project-tasks">{active.map(row)}
        {!foldHistory ? closed.map(row) : closed.length > 0 && <div className="quest-closed-group"><button type="button" className="quest-closed-toggle" aria-expanded={closedExpanded} aria-controls={closedId} onClick={() => setClosedExpanded((value) => !value)} onKeyDown={(event) => {
          if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229 || event.altKey || event.ctrlKey || event.metaKey) return;
          if (event.key === "ArrowRight") { event.preventDefault(); setClosedExpanded(true); }
          if (event.key === "ArrowLeft") { event.preventDefault(); setClosedExpanded(false); }
        }}><svg data-chevron viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m6 4 4 4-4 4" /></svg><span>已结束</span><small>{closed.length}</small></button><QuestGroupDisclosure open={closedExpanded} id={closedId}>{closed.map(row)}</QuestGroupDisclosure></div>}
      </div>
    </QuestGroupDisclosure>
  </section>;
}

export default function QuestWorkspace({
  quests, selected, selectedId, connected, loading = false, detailRequest = null, error, hasMore, onSelect, onLoadMore,
  plan, planNotice, blocked, onApprove, grants, workspaceId = "", workspaceRoot = "", workspaceName = "",
}: QuestWorkspaceProps) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<QuestFilter>("all");
  const [openedId, setOpenedId] = useState<string | null>(detailRequest?.id || null);
  const returnTarget = useRef<HTMLButtonElement | null>(null);
  const returnId = useRef<string | null>(null);
  const list = useRef<HTMLDivElement>(null);
  const listScroll = useRef(0);
  const detailHeading = useRef<HTMLHeadingElement>(null);
  const showingDetail = openedId !== null;
  const openTask = (id: string, trigger: HTMLButtonElement) => {
    returnTarget.current = trigger;
    listScroll.current = trigger.closest(".quest-body")?.scrollTop ?? 0;
    setOpenedId(id);
    onSelect(id);
  };
  const closeTask = () => { returnId.current = openedId; setOpenedId(null); };
  useLayoutEffect(() => {
    if (showingDetail) detailHeading.current?.focus();
    else if (returnId.current !== null) {
      const visible = (element: HTMLElement | null) => element?.isConnected && !element.closest("[inert]") && element.getClientRects().length > 0;
      const trigger = returnTarget.current;
      const match = Array.from(list.current?.querySelectorAll<HTMLButtonElement>("button[data-task-id]") || [])
        .find(button => button.dataset.taskId === returnId.current && visible(button));
      const inList = visible(trigger) && trigger?.dataset.taskId === returnId.current;
      const target = inList ? trigger : match || list.current;
      if (inList) {
        const body = trigger?.closest(".quest-body");
        if (body) body.scrollTop = listScroll.current;
      }
      target?.focus({ preventScroll: !!inList });
      returnId.current = null;
      returnTarget.current = null;
    }
  }, [showingDetail]);
  useLayoutEffect(() => { if (detailRequest) setOpenedId(detailRequest.id || null); }, [detailRequest]);
  const searchInput = useRef<HTMLInputElement>(null);
  const detailRegion = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => { if (detailRegion.current) detailRegion.current.scrollTop = 0; }, [selectedId]);
  const search = query.trim().toLocaleLowerCase();
  const loadedProjects = new Map<string, QuestProject>();
  const namedProjects = new Set<string>();
  for (const quest of quests) {
    const descriptor = projectFor(quest, workspaceId, workspaceRoot, workspaceName);
    const existing = loadedProjects.get(descriptor.key);
    if (!existing) loadedProjects.set(descriptor.key, { ...descriptor, quests: [quest], loaded: 1 });
    else {
      existing.quests.push(quest); existing.loaded += 1;
      if (!existing.path && descriptor.path) existing.path = descriptor.path;
      if (!namedProjects.has(descriptor.key) && explicitProjectName(quest)) existing.name = descriptor.name;
    }
    if (explicitProjectName(quest)) namedProjects.add(descriptor.key);
  }
  const filtered = quests.filter((quest) => {
    const descriptor = projectFor(quest, workspaceId, workspaceRoot, workspaceName);
    const project = loadedProjects.get(descriptor.key)!;
    const matchesSearch = !search || `${questDisplayTitle(quest.title, quest.id, quest.goal)} ${quest.goal} ${project.name}`.toLocaleLowerCase().includes(search);
    const matchesStatus = filter === "all" || (filter === "active" && !isClosedQuest(quest.status)) ||
      (filter === "attention" && needsQuestAttention(quest.status)) || (filter === "closed" && isClosedQuest(quest.status));
    return matchesSearch && matchesStatus;
  });
  const projects = new Map<string, QuestProject>();
  for (const quest of filtered) {
    const descriptor = projectFor(quest, workspaceId, workspaceRoot, workspaceName);
    const project = loadedProjects.get(descriptor.key)!;
    if (!projects.has(project.key)) projects.set(project.key, { ...project, quests: [] });
    projects.get(project.key)!.quests.push(quest);
  }
  const clearSearch = () => { setQuery(""); searchInput.current?.focus(); };
  const resetFilters = () => { setQuery(""); setFilter("all"); searchInput.current?.focus(); };
  const navigateList = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229 || event.altKey || event.ctrlKey || event.metaKey) return;
    if (!["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key) || !(event.target instanceof HTMLElement)) return;
    const current = event.target.closest<HTMLButtonElement>("button.list-row");
    if (!current || !event.currentTarget.contains(current)) return;
    const buttons = [...event.currentTarget.querySelectorAll<HTMLButtonElement>("button.list-row")].filter((button) => !button.closest("[inert]") && button.getClientRects().length > 0);
    const index = buttons.indexOf(current);
    const nextIndex = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1
      : Math.max(0, Math.min(buttons.length - 1, index + (event.key === "ArrowDown" ? 1 : -1)));
    const next = buttons[nextIndex];
    if (!next) return;
    event.preventDefault();
    next.focus();
  };
  const nodes = records(plan?.nodes);
  const emptyTitle = search ? "没有找到相关任务" : filter === "attention" ? "没有需要处理的任务" : filter === "active" ? "没有进行中的任务" : filter === "closed" ? "还没有已结束的任务" : "还没有任务";
  return <div className={`quest-workspace${showingDetail ? " is-detail" : ""}`}>
    <div className="quest-list" ref={list} tabIndex={-1} aria-label="任务列表" hidden={showingDetail} aria-busy={loading}>
      {(quests.length > 5 || hasMore || query || filter !== "all") && <details className="quest-find">
      <summary>查找任务{query || filter !== "all" ? " · 已筛选" : ""}</summary>
      <div className="quest-search">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 4 4" /></svg>
        <input ref={searchInput} type="search" aria-label="搜索任务" placeholder="搜索任务" value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => {
          if (event.key === "Escape" && !event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229) { event.preventDefault(); clearSearch(); }
        }} />
        {query && <button type="button" className="icon-button" aria-label="清除任务搜索" onClick={clearSearch}>×</button>}
      </div>
      <div className="quest-filters">{QUEST_FILTERS.map((item) => <button type="button" key={item.id} className={`text-button${filter === item.id ? " active" : ""}`} aria-pressed={filter === item.id} onClick={() => setFilter(item.id)}>{item.label}</button>)}</div>
      {quests.length > 0 && <p className="notice quest-filter-count">{filtered.length} 项任务{hasMore ? " · 仅显示已加载的任务" : ""}{loading ? " · 正在更新…" : ""}</p>}
      </details>}
      {error && <p className="notice error" role="alert">任务列表加载失败：{error}</p>}
      {!quests.length ? <div className="quest-empty" role="status">
        <p>{!connected ? "连接后查看任务" : loading ? "正在加载任务…" : error ? "暂时无法加载任务" : "还没有任务"}</p>
        <p className="notice">{!connected ? "连接恢复后，任务记录会显示在这里。" : loading ? "请稍候。" : error ? "连接恢复后将重新获取任务记录。" : "告诉 FORGE 你想做什么，任务会在这里显示。"}</p>
      </div> : !filtered.length ? <div className="quest-empty quest-filter-empty">
        <p>{emptyTitle}</p><p className="notice">{hasMore ? "你可以加载更多任务，或调整筛选。" : search ? "试试其他关键词。" : "其他任务可以在「全部」中查看。"}</p>
        <button type="button" className="text-button" onClick={resetFilters}>清除筛选</button>
      </div> : <div className="list quest-project-list" onKeyDown={navigateList}>{[...projects.values()].map((project) => <QuestProjectGroup key={project.key} project={project} selectedId={selectedId} search={search} filter={filter} showHeading={loadedProjects.size > 1} onSelect={openTask} />)}</div>}
      {hasMore && <button type="button" className="text-button more" disabled={loading} onClick={onLoadMore}>{loading ? "正在加载…" : "加载更多任务"}</button>}
    </div>

    {showingDetail && <div className="quest-detail" ref={detailRegion} onKeyDown={event => {
      if (event.key !== "Escape" || event.defaultPrevented || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
      event.preventDefault();
      closeTask();
    }}>
      <button type="button" className="text-button quest-back" onClick={closeTask}><ShellIcon name="chevronRight" size={16} />返回任务列表</button>
      <h3 className="quest-detail-heading" ref={detailHeading} tabIndex={-1}>{selected?.id === openedId ? questDisplayTitle(selected.title, selected.id, selected.goal) : "任务详情"}</h3>
      {!selected || selected.id !== openedId ? <div className="quest-empty" role="status"><p>{loading ? "正在加载任务…" : "此任务暂时不可用"}</p><p className="notice">返回列表查看最新任务。</p></div> : <>
        <div className="detail quest-overview">
          <div className="quest-phase"><strong className="quest-state" data-state={selected.status}>{statusLabel(selected.status)}</strong></div>
          {typeof selected.goal === "string" && selected.goal.trim() && selected.goal.trim() !== questDisplayTitle(selected.title, selected.id, selected.goal) && <p className="quest-goal">{selected.goal}</p>}
          {selected.status !== "completed" && <p className="notice">{QUEST_PHASES[selected.status] || "当前阶段以服务端原始状态为准。"}</p>}
        </div>

        <section className="quest-plan">
          <div className="section-heading spaced"><span>执行计划</span></div>
          {planNotice}
          {plan && <>
            <div className="quest-plan-nodes">{nodes.map((node, index) => <div className="plan-node" key={text(node.key, String(index))}>
              <div className="plan-node-heading"><strong>{index + 1}. {text(node.objective)}</strong><span className="state-label">{statusLabel(node.status)}</span></div>
              <details className="quest-step-details"><summary>步骤详情</summary><small>Token 预算：{text(node.budget_tokens)}</small><small>允许工具：{namesLabel(node.allowed_tools)}</small><small>验收条件：{criteria(node.success_criteria)}</small></details>
            </div>)}</div>
            <details className="quest-technical"><summary>技术详情</summary><dl><dt>任务 ID</dt><dd className="mono break">{selected.id}</dd><dt>任务版本</dt><dd>{selected.version}</dd><dt>计划版本</dt><dd>{text(plan.plan_revision)}</dd><dt>状态</dt><dd>{statusLabel(plan.status)}</dd><dt>总 Token 预算</dt><dd>{text(plan.max_total_tokens)}</dd><dt>费用上限</dt><dd>{costLimit(plan)}</dd><dt>计划摘要</dt><dd className="mono break">{text(plan.digest)}</dd></dl><pre>{JSON.stringify(plan, null, 2)}</pre></details>
            {plan.status === "proposed" && typeof plan.digest === "string" && <div className="button-row"><button type="button" className="button primary" disabled={blocked} onClick={onApprove}>批准此计划</button></div>}
          </>}
          {!plan && <details className="quest-technical"><summary>技术详情</summary><dl><dt>任务 ID</dt><dd className="mono break">{selected.id}</dd><dt>原始状态</dt><dd>{selected.status}</dd></dl></details>}
        </section>
      </>}
      {selected?.id === openedId && <details className="quest-grants"><summary>任务操作与权限</summary>{grants}</details>}
    </div>}
  </div>;
}
