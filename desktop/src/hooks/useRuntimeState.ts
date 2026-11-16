import { useRef, useState } from "react";
import { type Connection } from "../api";
import { isTauri } from "@tauri-apps/api/core";
import { Row, NativeStatus, WorkspaceOperation } from "../workspaceTypes";

/** State retained independently of mounted workspace panels. */
export default function useRuntimeState() {
  const native = isTauri();
  const [connection, setConnection] = useState<Connection | null>(null);
  const [connectionError, setConnectionError] = useState("");
  const [runtimeStatus, setRuntimeStatus] = useState<NativeStatus | null>(null);
  const [coreSnapshot, setCoreSnapshot] = useState<{ key: string; settings: Row; router: Row }>({ key: "", settings: {}, router: {} });
  const [providerId, setProviderId] = useState("");
  const [credentialRef, setCredentialRef] = useState("");
  const [credentialStatus, setCredentialStatus] = useState("");
  const [workspacePath, setWorkspacePath] = useState("");
  const [workspaceOperation, setWorkspaceOperation] = useState<WorkspaceOperation | null>(null);
  const [reviewReason, setReviewReason] = useState("");
  const [restoreFenced, setRestoreFenced] = useState<boolean | null>(null);
  const [restoreStatus, setRestoreStatus] = useState<Row | null>(null);
  const secretInput = useRef<HTMLInputElement>(null);
  const credentialInput = useRef<HTMLInputElement>(null);
  const nativeIdentity = useRef<{ generation: string; workspace: string } | null>(null);
  const nativePollSerial = useRef(0);
  const operationPollSerial = useRef(0);
  const maintenanceSubmitting = useRef(false);
  return { native, connection, setConnection, connectionError, setConnectionError, runtimeStatus, setRuntimeStatus, coreSnapshot, setCoreSnapshot, providerId, setProviderId, credentialRef, setCredentialRef, credentialStatus, setCredentialStatus, workspacePath, setWorkspacePath, workspaceOperation, setWorkspaceOperation, reviewReason, setReviewReason, restoreFenced, setRestoreFenced, restoreStatus, setRestoreStatus, secretInput, credentialInput, nativeIdentity, nativePollSerial, operationPollSerial, maintenanceSubmitting };
}

