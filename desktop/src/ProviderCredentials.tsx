import { useEffect, useState, type RefObject } from "react";

export interface ProviderCredentialsProps {
  providerId: string;
  onProviderId: (value: string) => void;
  credentialRef: string;
  onCredentialRef: (value: string) => void;
  inputRef: RefObject<HTMLInputElement>;
  status: string;
  blocked: boolean;
  onAction: (action: "set" | "exists" | "delete") => void;
}

/** The password stays in the caller-owned input; credential actions remain with the caller. */
export default function ProviderCredentials({ providerId, onProviderId, credentialRef, onCredentialRef, inputRef, status, blocked, onAction }: ProviderCredentialsProps) {
  const [optionsOpen, setOptionsOpen] = useState(() => !providerId.trim() || !credentialRef.trim());
  useEffect(() => {
    if (!providerId.trim() || !credentialRef.trim() || status.includes("填写有效")) setOptionsOpen(true);
  }, [providerId, credentialRef, status]);
  const act = (action: "set" | "exists" | "delete") => {
    if (!/^[A-Za-z0-9_-]{1,100}$/.test(providerId) || !/^FORGE_PROVIDER_KEY_[A-Z0-9_]{1,128}$/.test(credentialRef)) setOptionsOpen(true);
    onAction(action);
  };

  return <div className="credential-form">
    <label className="field-label">API Key<input ref={inputRef} aria-label="新凭据" type="password" autoComplete="new-password" /></label>
    <div className="credential-primary-actions">
      <div className="button-row"><button className="button primary" type="button" disabled={blocked} onClick={() => act("set")}>保存</button><button className="button" type="button" onClick={() => act("exists")}>检查是否存在</button></div>
      <p className="credential-hint">保存密钥会重启本地 Core；检查仅检查本机凭据。</p>
    </div>
    <details className="settings-disclosure credential-options" open={optionsOpen} onToggle={(event) => setOptionsOpen(event.currentTarget.open)}>
      <summary>凭据选项</summary>
      <div className="settings-disclosure-body">
        <label className="field-label">供应商 ID<input value={providerId} onChange={(event) => onProviderId(event.target.value)} autoComplete="off" /></label>
        <label className="field-label">凭据引用<input value={credentialRef} onChange={(event) => onCredentialRef(event.target.value)} autoComplete="off" /></label>
        <div className="button-row"><button className="button" type="button" disabled={blocked} onClick={() => act("delete")}>删除</button></div>
      </div>
    </details>
    {status && <p className="notice" role="status">{status}</p>}
  </div>;
}
