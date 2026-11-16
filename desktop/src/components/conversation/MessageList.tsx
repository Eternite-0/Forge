import type { WorkerRun } from "../../ConversationRuntime";
import type { Outgoing } from "../../timeline";
import UserMessage from "./UserMessage";
import AssistantMessage from "./AssistantMessage";
import ProcessTranscript, { ProcessFileChanges } from "./ProcessTranscript";
import { buildProcessTimeline } from "../../processTimeline";
import { type DisplayItem, type EventViewMemory, type HistoryProps, type Quest } from "./eventModel";
import { Fragment } from "react";

export interface MessageListProps extends HistoryProps {
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
export default function MessageList({ items, identity, quests, workerRuns, memory, native, canRetry, onRetry, onQuest, onOpenFile, onInspect, ...history }: MessageListProps) {
  const entries = buildProcessTimeline(items, workerRuns);
  const changesForAnswer = new Map(entries.filter(item => item.type === "process" && item.answerId).map(item => item.type === "process" ? [item.answerId!, item] : ["", null]));
  return <div className="conversation-list">{entries.map((item) => {
    if (item.type === "process") return <Fragment key={`${identity}|process:${item.key}`}><ProcessTranscript process={item} runs={workerRuns} quests={quests} memory={memory} native={native} onInspect={onInspect} onQuest={onQuest} onOpenFile={onOpenFile} {...history} />
      {!item.answerId && <ProcessFileChanges process={item} memory={memory} onInspect={onInspect} onOpenFile={onOpenFile} {...history} />}</Fragment>;
    if (item.type === "outgoing") return <UserMessage key={`outgoing:${item.value.requestId}`} outgoing={item.value} native={native} onInspect={onInspect} canRetry={canRetry} onRetry={onRetry} />;
    const process = changesForAnswer.get(item.value.id);
    return <Fragment key={item.value.id}>{item.value.role === "user" ? <UserMessage message={item.value} native={native} onInspect={onInspect} />
      : <AssistantMessage message={item.value} native={native} onInspect={onInspect} />}{process && <ProcessFileChanges process={process} memory={memory} onInspect={onInspect} onOpenFile={onOpenFile} {...history} />}</Fragment>;
  })}</div>;
}

