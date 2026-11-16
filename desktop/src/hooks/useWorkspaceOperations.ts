import { useEffect } from "react";
import { invoke } from "@tauri-apps/api/core";
import { errorMessage } from "../logic";
import { Row, WorkspaceOperation } from "../workspaceTypes";
import type { WorkspaceState } from "./useWorkspaceState";
import useForgeConnection from "./useForgeConnection";

export default function useWorkspaceOperations(workspace: WorkspaceState & ReturnType<typeof useForgeConnection>) {
  const { native, providerId, credentialRef, setCredentialStatus, workspacePath, workspaceOperation, setWorkspaceOperation, reviewReason, setReviewReason, credentialInput, operationPollSerial, maintenanceSubmitting, setActionError, actionBusy, setActionBusy, writesBlocked, syncNative } = workspace;
  useEffect(() => {
    if (!native) return;
    let active = true;
    const poll = async () => {
      if (maintenanceSubmitting.current) return;
      const serial = ++operationPollSerial.current;
      try {
        const value = await invoke<WorkspaceOperation>("workspace_operation_status", {});
        if (active && serial === operationPollSerial.current && !maintenanceSubmitting.current) setWorkspaceOperation(value);
      } catch (error) {
        if (active && serial === operationPollSerial.current && !String(error).includes("OPERATION_NOT_FOUND")) setWorkspaceOperation({ operationId: "", operation: "restore", status: "unknown" });
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 3000);
    return () => { active = false; operationPollSerial.current += 1; window.clearInterval(timer); };
  }, [native]);
  const credential = async (operation: "set" | "delete" | "exists") => {
    if ((writesBlocked || maintenanceSubmitting.current) && operation !== "exists") return;
    if (!native || !/^[A-Za-z0-9_-]{1,100}$/.test(providerId) || !/^FORGE_PROVIDER_KEY_[A-Z0-9_]{1,128}$/.test(credentialRef)) { setCredentialStatus("请填写有效的供应商 ID 与凭据引用。"); return; }
    const secret = credentialInput.current?.value || "";
    if (credentialInput.current) credentialInput.current.value = "";
    if (operation === "set" && !secret) { setCredentialStatus("请输入凭据。"); return; }
    if (operation === "delete" && !window.confirm(`删除 ${providerId} / ${credentialRef} 的已保存凭据？`)) return;
    try {
      const result = await invoke<Row>(`credential_${operation}`, { providerId, credentialRef, ...(operation === "set" ? { secret } : {}) });
      setCredentialStatus(operation === "exists" ? (result.exists ? "凭据已保存。" : "未找到凭据。") : operation === "set" ? "凭据已保存。" : result.deleted ? "凭据已删除。" : "未找到凭据。");
    } catch (error) { setCredentialStatus(errorMessage(error)); }
  };
  const maintainWorkspace = async (operation: "backup" | "restore") => {
    if (!native || writesBlocked || actionBusy || maintenanceSubmitting.current) return;
    const path = workspacePath.trim();
    if (!path || !/^(?:[a-zA-Z]:[\\/]|\\\\)/.test(path)) { setActionError("请输入 Windows 绝对目录路径。"); return; }
    const question = operation === "restore"
      ? `恢复工作空间：${path}\n恢复将替换当前工作空间，并在完成后锁定写操作，直到人工核对。继续吗？`
      : `备份工作空间到：${path}\n维护期间 Core 会暂时停止。继续吗？`;
    if (!window.confirm(question)) return;
    setActionError("");
    operationPollSerial.current += 1;
    maintenanceSubmitting.current = true;
    setWorkspaceOperation({ operationId: "", operation, status: "unknown" });
    try { setWorkspaceOperation(await invoke<WorkspaceOperation>(`workspace_${operation}`, { path })); }
    catch { setActionError("无法确认维护请求是否已启动。请等待状态查询，结果未知时不要重试。"); }
    finally { maintenanceSubmitting.current = false; }
  };

  const acknowledgeUnknown = async () => {
    if (!native || workspaceOperation?.status !== "unknown" || !workspaceOperation.operationId || !reviewReason.trim() || actionBusy) return;
    if (!window.confirm(`归档未知维护结果 ${workspaceOperation.operationId}？\n请先核对工作区和备份。此操作不表示维护成功，也不会解除恢复锁定或预算限制。`)) return;
    operationPollSerial.current += 1; maintenanceSubmitting.current = true; setActionBusy(true); setActionError("");
    try { setWorkspaceOperation(await invoke<WorkspaceOperation>("workspace_operation_acknowledge", { operationId: workspaceOperation.operationId, reason: reviewReason.trim() })); setReviewReason(""); }
    catch (error) { setActionError(errorMessage(error)); }
    finally { maintenanceSubmitting.current = false; setActionBusy(false); }
  };

  const retryRuntime = async () => {
    if (!native || writesBlocked || actionBusy || !window.confirm("重启本地 Core？正在运行的任务可能中断。")) return;
    setActionBusy(true); setActionError("");
    try { await invoke("runtime_retry"); await syncNative(); }
    catch (error) { setActionError(errorMessage(error)); }
    finally { setActionBusy(false); }
  };
  return { credential, maintainWorkspace, acknowledgeUnknown, retryRuntime };
}

