import { useRef, useState } from "react";
import { type PendingMessage } from "../logic";
import { type ChatMessage as Message, type Outgoing, type TimelineEvent } from "../timeline";

/** State retained independently of mounted workspace panels. */
export default function useConversationState() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [timelineEvents, setTimelineEvents] = useState<TimelineEvent[]>([]);
  const [timelineBefore, setTimelineBefore] = useState<string | null>(null);
  const [timelineHasMore, setTimelineHasMore] = useState(false);
  const [timelineError, setTimelineError] = useState("");
  const [outgoing, setOutgoing] = useState<Outgoing[]>([]);
  const [hasNew, setHasNew] = useState(false);
  const [readingPaused, setReadingPaused] = useState(false);
  const [before, setBefore] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [historyBusy, setHistoryBusy] = useState(false);
  const [conversationError, setConversationError] = useState("");
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState<PendingMessage | null>(null);
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const composerInput = useRef<HTMLTextAreaElement>(null);
  const composerResizeFrame = useRef(0);
  const followBottom = useRef(true);
  const manualPause = useRef(false);
  const historyAnchor = useRef<{ top: number; height: number } | null>(null);
  const readingAnchor = useRef<{ key: string; offset: number; top: number } | null>(null);
  const seenMessages = useRef(new Set<string>());
  const seenEvents = useRef(new Set<string>());
  const seenInitialized = useRef(false);
  const historyLoaded = useRef(false);
  const questsLoaded = useRef(false);
  return { messages, setMessages, timelineEvents, setTimelineEvents, timelineBefore, setTimelineBefore, timelineHasMore, setTimelineHasMore, timelineError, setTimelineError, outgoing, setOutgoing, hasNew, setHasNew, readingPaused, setReadingPaused, before, setBefore, hasMore, setHasMore, historyBusy, setHistoryBusy, conversationError, setConversationError, draft, setDraft, pending, setPending, sending, setSending, sendError, setSendError, scrollRef, composerInput, composerResizeFrame, followBottom, manualPause, historyAnchor, readingAnchor, seenMessages, seenEvents, seenInitialized, historyLoaded, questsLoaded };
}

