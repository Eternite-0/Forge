import { useRef, useState } from "react";
import { RequestGuard } from "../logic";
import { Row, Quest, DataState, EMPTY } from "../workspaceTypes";

/** State retained independently of mounted workspace panels. */
export default function useInspectorState() {
  const [refreshing, setRefreshing] = useState(false);
  const [suggestedRouterModel, setSuggestedRouterModel] = useState<string | null>(null);
  const [quests, setQuests] = useState<Quest[]>([]);
  const [questsBefore, setQuestsBefore] = useState<string | null>(null);
  const [questLoadError, setQuestLoadError] = useState("");
  const [pendingApprovals, setPendingApprovals] = useState<Row[]>([]);
  const [approvalLoadError, setApprovalLoadError] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [panel, setPanel] = useState<DataState>(EMPTY);
  const [panelContext, setPanelContext] = useState("");
  const [questPlan, setQuestPlan] = useState<DataState>(EMPTY);
  const [planRefresh, setPlanRefresh] = useState(0);
  const [planContext, setPlanContext] = useState("");
  const [grants, setGrants] = useState<DataState>(EMPTY);
  const [grantContext, setGrantContext] = useState("");
  const [grantScope, setGrantScope] = useState("");
  const [workspaceRoot, setWorkspaceRoot] = useState("");
  const [actionError, setActionError] = useState("");
  const [actionBusy, setActionBusy] = useState(false);
  const [actionNotice, setActionNotice] = useState("");
  const [memoryQuery, setMemoryQuery] = useState("");
  const [memorySearch, setMemorySearch] = useState("");
  const [memoryCursor, setMemoryCursor] = useState<string | null>(null);
  const [activityCursor, setActivityCursor] = useState<string | null>(null);
  const [approvalCursor, setApprovalCursor] = useState<string | null>(null);
  const [modelEditor, setModelEditor] = useState<{ initial: Row | null; version: string | null } | null>(null);
  const refreshSerial = useRef(0);
  const requestGuard = useRef(new RequestGuard());
  const guard = requestGuard.current;
  return { suggestedRouterModel, setSuggestedRouterModel, refreshing, setRefreshing, quests, setQuests, questsBefore, setQuestsBefore, questLoadError, setQuestLoadError, pendingApprovals, setPendingApprovals, approvalLoadError, setApprovalLoadError, selectedId, setSelectedId, panel, setPanel, panelContext, setPanelContext, questPlan, setQuestPlan, planRefresh, setPlanRefresh, planContext, setPlanContext, grants, setGrants, grantContext, setGrantContext, grantScope, setGrantScope, workspaceRoot, setWorkspaceRoot, actionError, setActionError, actionBusy, setActionBusy, actionNotice, setActionNotice, memoryQuery, setMemoryQuery, memorySearch, setMemorySearch, memoryCursor, setMemoryCursor, activityCursor, setActivityCursor, approvalCursor, setApprovalCursor, modelEditor, setModelEditor, refreshSerial, requestGuard, guard };
}

