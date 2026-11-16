import { useEffect } from "react";
import { EMPTY, text, asRecord } from "../workspaceTypes";
import { useWorkerActivity } from "../ConversationRuntime";
import { captureTimelineReading } from "../captureTimelineReading";
import useRuntimeState from "./useRuntimeState";
import useWorkspaceNavigation from "./useWorkspaceNavigation";
import useConversationState from "./useConversationState";
import useInspectorState from "./useInspectorState";

/** Compose the four state domains and scope their request guards. */
export default function useWorkspaceState() {
  const state = { ...useRuntimeState(), ...useWorkspaceNavigation(), ...useConversationState(), ...useInspectorState() };
  const { native, connection, runtimeStatus, coreSnapshot, setProviderId, setCredentialRef, setCredentialStatus, workspaceOperation, restoreFenced, credentialInput, view, quests, selectedId, panel, panelContext, questPlan, planContext, grants, grantContext, memorySearch, guard } = state;
  const connectionKey = connection ? `${connection.base_url}|${connection.workspace_id || ""}|${connection.generation}` : "";
  const coreInfo = coreSnapshot.key === connectionKey ? coreSnapshot.settings : {};
  const routerInfo = coreSnapshot.key === connectionKey ? coreSnapshot.router : {};
  const workerActivity = useWorkerActivity(connection, () => {
    const anchor = captureTimelineReading(state.scrollRef.current, state.followBottom.current);
    if (anchor) state.readingAnchor.current = anchor;
  });
  const savedCredentialProvider = asRecord(asRecord(coreInfo.config).provider);
  const savedProviderId = text(savedCredentialProvider.id, "");
  const savedCredentialRef = text(savedCredentialProvider.credential_ref, "");
  useEffect(() => {
    setProviderId(savedProviderId);
    setCredentialRef(savedCredentialRef);
    setCredentialStatus("");
    if (credentialInput.current) credentialInput.current.value = "";
  }, [connectionKey, savedProviderId, savedCredentialRef]);
  guard.setConnection(connection ? `${connection.base_url}|${connection.workspace_id || ""}|${connection.generation}` : "");
  guard.setContext("panel", `${view}|${selectedId}|${memorySearch}`);
  guard.setContext("grants", `${view}|${selectedId}`);
  guard.setContext("action", `${view}|${selectedId}`);
  guard.setContext("model", view);
  guard.setContext("selected-quest", selectedId);
  useEffect(() => { guard.activate(); return () => guard.dispose(); }, [guard]);
  const selected = quests.find((q) => q.id === selectedId);
  const contextKey = `${connection?.generation || ""}|${view}|${selectedId}|${memorySearch}`;
  const questContextKey = `${connection?.generation || ""}|${selectedId}`;
  const visiblePanel = panelContext === contextKey ? panel : EMPTY;
  const visiblePlan = planContext === questContextKey ? questPlan : EMPTY;
  const visibleGrants = grantContext === `${questContextKey}|${view}` ? grants : EMPTY;
  const writesBlocked = restoreFenced === true || (native && (restoreFenced === null || runtimeStatus?.state === "maintenance" || workspaceOperation?.status === "running" || (workspaceOperation?.status === "unknown" && !workspaceOperation.acknowledged)));
  return { ...state, connectionKey, coreInfo, routerInfo, workerActivity, savedCredentialProvider, savedProviderId, savedCredentialRef, selected, contextKey, questContextKey, visiblePanel, visiblePlan, visibleGrants, writesBlocked };
}
export type WorkspaceState = ReturnType<typeof useWorkspaceState>;

