import { visibleConversationMessage } from "../../conversationPresentation";
import type { WorkerRun } from "../../ConversationRuntime";
import type { Outgoing } from "../../timeline";
import UserMessage from "./UserMessage";
import AssistantMessage from "./AssistantMessage";
import ProcessTranscript, { ProcessFileChanges } from "./ProcessTranscript";
import { buildProcessTimeline } from "../../processTimeline";
import { type DisplayItem, type EventViewMemory, type HistoryProps, type Quest } from "./eventModel";
import { Fragment } from "react";

export interface MessageListProps extends HistoryProps {
  mode?: "conversation" | "execution";
  items: DisplayItem[];
  identity: string;
  quests: Quest[];
  workerRuns: WorkerRun[];
  memory: EventViewMemory;
  native: boolean;
  canRetry: boolean;
  onRetry: (item: Outgoing) => void;
  onQuest: (id: string) => void;
  onOpenFile?: (path: string) => void;
  onInspect: () => void;
}

/** Keep persisted ordering and stable keys while components own their individual rendering. */
export default function MessageList({ mode = "conversation", items, identity, quests, workerRuns, memory, native, canRetry, onRetry, onQuest, onOpenFile, onInspect, ...history }: MessageListProps) {
  const entries = buildProcessTimeline(items, workerRuns);
  const changesForAnswer = new Map(entries.filter(item => item.type === "process" && item.answerId).map(item => item.type === "process" ? [item.answerId!, item] : ["", null]));
  return <div className="conversation-list">{entries.map((item) => {
    if (mode === "execution" && item.type !== "process") return null;
    if (mode === "conversation" && item.type === "process") return <Fragment key={`${identity}|visible:${item.key}`}>
      {item.records.filter(record => record.type === "message" && visibleConversationMessage(record.value)).map(record => record.type === "message" ? <AssistantMessage key={record.value.id} message={record.value} native={native} onInspect={onInspect} /> : null)}
      {!item.answerId && <ProcessFileChanges process={item} memory={memory} onInspect={onInspect} onOpenFile={onOpenFile} {...history} />}
    </Fragment>;
    if (item.type === "process") return <ProcessTranscript key={`${identity}|process:${item.key}`} process={item} runs={workerRuns} quests={quests} memory={memory} native={native} onInspect={onInspect} onQuest={onQuest} onOpenFile={onOpenFile} {...history} />;
    if (item.type === "outgoing") return <UserMessage key={`outgoing:${item.value.requestId}`} outgoing={item.value} native={native} onInspect={onInspect} canRetry={canRetry} onRetry={onRetry} />;
    if (!visibleConversationMessage(item.value)) return null;
    const process = changesForAnswer.get(item.value.id);
    return <Fragment key={item.value.id}>{item.value.role === "user" ? <UserMessage message={item.value} native={native} onInspect={onInspect} />
      : <AssistantMessage message={item.value} native={native} onInspect={onInspect} />}{process && <ProcessFileChanges process={process} memory={memory} onInspect={onInspect} onOpenFile={onOpenFile} {...history} />}</Fragment>;
  })}</div>;
}

