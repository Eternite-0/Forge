import { useId, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { isClosedQuest, namesLabel, needsQuestAttention, statusLabel } from "./presentation";
import { explicitProjectName, projectFor, type ProjectFields } from "./projectContext";

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
  proposed: "目标已提出，后续计划与团队仍以实际提案为准。",
  awaiting_approval: "任务正在等待批准。请核对计划、任务团队及审批中心中需要确认的内容。",
  approved: "任务已批准；执行仍需满足当前计划、团队、工具权限与预算约束。",
  queued: "任务已进入队列，等待满足资源与执行条件。",
  running: "任务处于运行阶段。运行活动中可查看已记录的成员执行情况。",
  blocked: "任务已阻塞。请结合运行活动、审批与权限记录核对原因。",
  paused: "任务已暂停，当前记录保留在此工作空间。",
  needs_input: "任务需要补充信息，请回到长期对话继续说明。",
  reviewing: "任务正在复核。请以验证与最终任务状态判断结果。",
  completed: "任务状态为已完成，可通过计划与运行活动回查执行和验证记录。",
  failed: "任务已失败。请查看运行活动，并在长期对话中讨论后续处理。",
  cancelled: "任务已取消，历史计划与执行记录仍可回查。",
  rejected: "任务已拒绝，可查看历史计划与审批记录了解处理经过。",
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

function QuestProjectGroup({ project, selectedId, search, filter, onSelect }: { project: QuestProject; selectedId: string; search: string; filter: QuestFilter; onSelect: (id: string) => void }) {
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
  const row = (quest: WorkspaceQuest) => <button type="button" key={quest.id} className={selectedId === quest.id ? "list-row selected" : "list-row"}
    aria-label={`${quest.title} ${quest.id} ${quest.status}`} aria-current={selectedId === quest.id ? "page" : undefined} onClick={() => onSelect(quest.id)}>
    <span><strong>{quest.title}</strong><small>{quest.goal}</small></span><span className="state-label">{statusLabel(quest.status)}</span>
  </button>;
  return <section className="quest-project-group" data-project-key={project.key} data-project-source={project.source}>
    <button type="button" className="quest-project-toggle" aria-expanded={expanded} aria-controls={contentId} title={project.path || undefined} onClick={() => setExpanded((value) => !value)} onKeyDown={(event) => {
      if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229 || event.altKey || event.ctrlKey || event.metaKey) return;
      if (event.key === "ArrowRight") { event.preventDefault(); setExpanded(true); }
      if (event.key === "ArrowLeft") { event.preventDefault(); setExpanded(false); }
    }}><svg data-chevron viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m6 4 4 4-4 4" /></svg><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 7h7l2 2h9v12H3V7Zm0 0V4h7l3 3" /></svg><span>{project.name}</span><small title="当前已加载的任务数量">{project.loaded}</small></button>
    <QuestGroupDisclosure open={expanded} id={contentId}>
      <div className="quest-project-tasks">{active.map(row)}
        {closed.length > 0 && <div className="quest-closed-group"><button type="button" className="quest-closed-toggle" aria-expanded={closedExpanded} aria-controls={closedId} onClick={() => setClosedExpanded((value) => !value)} onKeyDown={(event) => {
          if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229 || event.altKey || event.ctrlKey || event.metaKey) return;
          if (event.key === "ArrowRight") { event.preventDefault(); setClosedExpanded(true); }
          if (event.key === "ArrowLeft") { event.preventDefault(); setClosedExpanded(false); }
        }}><svg data-chevron viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m6 4 4 4-4 4" /></svg><span>已结束</span><small>{closed.length}</small></button><QuestGroupDisclosure open={closedExpanded} id={closedId}>{closed.map(row)}</QuestGroupDisclosure></div>}
      </div>
    </QuestGroupDisclosure>
  </section>;
}

export default function QuestWorkspace({
  quests, selected, selectedId, connected, error, hasMore, onSelect, onLoadMore,
  plan, planNotice, blocked, onApprove, grants, workspaceId = "", workspaceRoot = "", workspaceName = "",
}: QuestWorkspaceProps) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<QuestFilter>("all");
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
    const matchesSearch = !search || `${quest.title} ${quest.goal} ${quest.id} ${project.name} ${project.path} ${project.key}`.toLocaleLowerCase().includes(search);
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
    if (next !== current) next.click();
  };
  const nodes = records(plan?.nodes);
  return <div className="quest-workspace">
    <div className="quest-list">
      <div className="section-heading"><span>后台任务</span><span>{quests.length} 项已加载{hasMore ? " · 还有更多" : ""}</span></div>
      <div className="quest-search">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 4 4" /></svg>
        <input ref={searchInput} type="search" aria-label="搜索任务" placeholder="搜索已加载的任务" value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => {
          if (event.key === "Escape" && !event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229) { event.preventDefault(); clearSearch(); }
        }} />
        {query && <button type="button" className="icon-button" aria-label="清除任务搜索" onClick={clearSearch}>×</button>}
      </div>
      <div className="quest-filters">{QUEST_FILTERS.map((item) => <button type="button" key={item.id} className={`text-button${filter === item.id ? " active" : ""}`} aria-pressed={filter === item.id} onClick={() => setFilter(item.id)}>{item.label}</button>)}</div>
      {quests.length > 0 && <p className="notice quest-filter-count">显示 {filtered.length} / {quests.length} 项已加载任务</p>}
      {error && <p className="notice error" role="alert">任务列表加载失败：{error}</p>}
      {!quests.length ? <div className="quest-empty">
        <p>{connected ? "暂无后台任务" : "连接 Core 后显示任务"}</p>
        <p className="notice">在长期对话中提出目标，计划、团队与执行记录会归到对应任务。</p>
      </div> : !filtered.length ? <div className="quest-empty quest-filter-empty">
        <p>没有匹配的已加载任务</p><p className="notice">试试其他关键词，或清除搜索与状态筛选。</p>
        <button type="button" className="text-button" onClick={resetFilters}>清除筛选</button>
      </div> : <div className="list quest-project-list" onKeyDown={navigateList}>{[...projects.values()].map((project) => <QuestProjectGroup key={project.key} project={project} selectedId={selectedId} search={search} filter={filter} onSelect={onSelect} />)}</div>}
      {hasMore && <button type="button" className="text-button more" onClick={onLoadMore}>加载更多任务</button>}
    </div>

    <div className="quest-detail" ref={detailRegion}>
      {!selected ? <div className="quest-empty quest-empty-selection"><h3>选择任务，查看计划与权限</h3><p className="notice">每个任务的计划、团队、审批与执行记录保持独立，结果回到同一个长期对话。</p></div> : <>
        <div className="detail quest-overview">
          <span className="eyebrow">当前任务</span>
          <h3>{selected.title}</h3>
          <p className="quest-goal">{selected.goal}</p>
          <div className="quest-phase"><strong>{statusLabel(selected.status)}</strong><span>版本 {selected.version}</span></div>
          <p className="notice">{QUEST_PHASES[selected.status] || "当前阶段以服务端原始状态为准。"}</p>
        </div>

        <section className="quest-plan">
          <div className="section-heading spaced"><span>执行计划</span>{plan && <span>{nodes.length} 个节点</span>}</div>
          {planNotice}
          {plan && <>
            <dl className="quest-plan-facts"><dt>计划版本</dt><dd>{text(plan.plan_revision)}</dd><dt>状态</dt><dd>{statusLabel(plan.status)}</dd><dt>总 Token 预算</dt><dd>{text(plan.max_total_tokens)}</dd><dt>费用上限</dt><dd>{costLimit(plan)}</dd></dl>
            <div className="quest-plan-nodes">{nodes.map((node, index) => <div className="plan-node" key={text(node.key, String(index))}>
              <div className="plan-node-heading"><strong>{index + 1}. {text(node.objective)}</strong><span className="state-label">{statusLabel(node.status)}</span></div>
              <small>Token 预算：{text(node.budget_tokens)}</small>
              <small>允许工具：{namesLabel(node.allowed_tools)}</small>
              <small>验收条件：{criteria(node.success_criteria)}</small>
            </div>)}</div>
            <details className="quest-technical"><summary>技术详情</summary><dl><dt>任务 ID</dt><dd className="mono break">{selected.id}</dd><dt>计划摘要</dt><dd className="mono break">{text(plan.digest)}</dd></dl><pre>{JSON.stringify(plan, null, 2)}</pre></details>
            {plan.status === "proposed" && typeof plan.digest === "string" && <div className="button-row"><button type="button" className="button primary" disabled={blocked} onClick={onApprove}>批准此计划</button></div>}
          </>}
          {!plan && <details className="quest-technical"><summary>技术详情</summary><dl><dt>任务 ID</dt><dd className="mono break">{selected.id}</dd><dt>原始状态</dt><dd>{selected.status}</dd></dl></details>}
        </section>
      </>}
      {grants}
    </div>
  </div>;
}
