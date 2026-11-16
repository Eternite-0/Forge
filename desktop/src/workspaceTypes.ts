import { retainIfEqual } from "./logic";
import type { SettingId } from "./SettingsView";
import type { ChatMessage as Message, TimelineEvent } from "./timeline";

export type View = "chat" | "quests" | "activity" | "models" | "teams" | "memory" | "approvals" | "settings";
export type Row = Record<string, unknown>;
export interface TimelinePage { items: TimelineEvent[]; next_before?: string | null; has_more?: boolean }
export interface Page { messages: Message[]; router?: Row; next_before?: string | null; has_more?: boolean }
export interface NativeConnection { state: string; baseUrl: string; bearer: string; workspaceId: string; generation: string }
export interface NativeStatus { state: string; generation: string; restartCount: number; lastErrorCode: string | null }
export interface WorkspaceOperation { operationId: string; operation: "backup" | "restore"; status: "running" | "completed" | "unknown"; acknowledged?: boolean; result?: { ok: boolean; operation: string; code?: string } }
export interface Quest extends Row { id: string; title: string; status: string; version: number; goal: string }
export interface QuestPage { items: Quest[]; next_before?: string | null; has_more?: boolean }
export interface MemoryItem extends Row { id: string; statement: string; source_refs: string[] }
export interface DataState { loading: boolean; error: string; value: unknown }

export const VIEW_NAMES: Record<View, string> = { chat: "对话", quests: "后台任务", activity: "运行活动", models: "模型库", teams: "任务团队", memory: "记忆中心", approvals: "审批中心", settings: "设置" };
export const SETTING_VIEWS: Partial<Record<SettingId, View>> = { models: "models", memory: "memory" };
export const TASK_VIEWS: View[] = ["quests", "teams", "activity", "approvals"];
export const EMPTY: DataState = { loading: false, error: "", value: null };
export const text = (value: unknown, fallback = "未知") => value == null || value === "" ? fallback : String(value);
export const rows = (value: unknown): Row[] => Array.isArray(value) ? value.filter((x): x is Row => !!x && typeof x === "object") : [];
export const asRecord = (value: unknown): Row => value && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
export const display = (value: unknown) => typeof value === "string" ? value : JSON.stringify(value, null, 2);
export const eventKey = (event: TimelineEvent) => `${event.kind}:${event.event_id}`;
export const mergeEvents = (current: TimelineEvent[], next: TimelineEvent[]) => retainIfEqual(current, [...new Map([...current, ...next].map((event) => [eventKey(event), event])).values()]);

