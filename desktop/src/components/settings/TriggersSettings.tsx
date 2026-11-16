import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { type Connection } from "../../api";
import { errorMessage } from "../../logic";
import { SettingsSection } from "../../SettingsView";
import { asRecord, rows, text, type Row } from "../../workspaceTypes";
import { fileWatchInput, scheduleInput, sourceInput, triggerInput, type AdmissionMode, type PayloadFormat } from "./settingsInputs";
import useSettingsRequests from "./useSettingsRequests";

type Operation = "source" | "event" | "schedule" | "watch";
const sourceKinds: Record<string, string> = { external: "外部事件", timer: "定时任务", file: "文件监听" };
const admissionModes: Record<AdmissionMode, string> = { candidate: "交给 Router 判断", explicit: "仅接收明确发给助手的事件", ignore: "忽略此源" };
const triggerStates: Record<string, string> = { pending: "等待 Router 处理", filtered: "已过滤", passed_through: "已交给订阅成员", routed: "已路由", completed: "已处理" };

function ContentInput({ label, value, onChange, format, onFormat }: { label: string; value: string; onChange: (value: string) => void; format: PayloadFormat; onFormat: (value: PayloadFormat) => void }) {
  return <><label className="field-label">内容格式<select aria-label="内容格式" value={format} onChange={(event) => onFormat(event.target.value as PayloadFormat)}><option value="text">文本</option><option value="json">结构化内容（JSON 对象）</option></select></label>
    <label className="field-label">{label}<textarea aria-label={label} rows={3} required value={value} onChange={(event) => onChange(event.target.value)} placeholder={format === "json" ? '{"content":"需要处理的内容"}' : "描述需要处理的事件"} /></label></>;
}

function TriggerRecord({ item }: { item: Row }) {
  let preview = text(item.preview, "");
  try { const payload = asRecord(JSON.parse(preview)); if (typeof payload.content === "string") preview = payload.content; } catch { /* A bounded preview may end in the middle of JSON. */ }
  return <div className="settings-result-record"><strong>{text(item.thread_key, text(item.id))}</strong><small>{text(item.source_id)} · {triggerStates[text(item.status)] || text(item.status)}</small>
    {preview && <p>{preview}</p>}<details className="settings-disclosure"><summary>事件记录</summary><dl><dt>事件标识</dt><dd className="break">{text(item.external_id)}</dd><dt>记录 ID</dt><dd className="break">{text(item.id)}</dd><dt>路由原因</dt><dd>{text(item.reason)}</dd><dt>重复接收</dt><dd>{item.duplicate === true ? "是" : "否"}</dd></dl></details></div>;
}

export default function TriggersSettings(props: { connection: Connection | null; blocked: boolean }) {
  const identity = props.connection ? `${props.connection.base_url}|${props.connection.workspace_id || ""}|${props.connection.generation}` : "disconnected";
  return <TriggerControls key={identity} {...props} />;
}

function TriggerControls({ connection, blocked }: { connection: Connection | null; blocked: boolean }) {
  const { run } = useSettingsRequests();
  const [pending, setPending] = useState<Row[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [busy, setBusy] = useState<Operation | null>(null);
  const mutationPending = useRef(false);
  const [error, setError] = useState<{ operation: Operation; message: string } | null>(null);
  const [notice, setNotice] = useState<{ operation: Operation; message: string } | null>(null);
  const [sources, setSources] = useState<Row[]>([]);
  const [source, setSource] = useState("");
  const [kind, setKind] = useState("external");
  const [mode, setMode] = useState<AdmissionMode>("candidate");
  const [actors, setActors] = useState("");
  const [eventSource, setEventSource] = useState("");
  const [externalId, setExternalId] = useState(() => `event_${crypto.randomUUID()}`);
  const [thread, setThread] = useState("");
  const [actor, setActor] = useState("");
  const [addressed, setAddressed] = useState(false);
  const [content, setContent] = useState("");
  const [format, setFormat] = useState<PayloadFormat>("text");
  const [eventReceipt, setEventReceipt] = useState<Row | null>(null);
  const [timerSource, setTimerSource] = useState("");
  const [timerThread, setTimerThread] = useState("");
  const [timerContent, setTimerContent] = useState("");
  const [timerFormat, setTimerFormat] = useState<PayloadFormat>("text");
  const [dueAt, setDueAt] = useState("");
  const [interval, setInterval] = useState("");
  const [timerReceipt, setTimerReceipt] = useState<Row | null>(null);
  const [fileSource, setFileSource] = useState("");
  const [filePath, setFilePath] = useState("");
  const [fileThread, setFileThread] = useState("");
  const [watchReceipt, setWatchReceipt] = useState<Row | null>(null);
  const sourceList = useId();
  const disabled = !connection || blocked || busy !== null;

  useEffect(() => {
    if (!connection) return;
    setLoading(true); setLoadError("");
    void run<Row[]>("pending", connection, "/api/triggers?limit=32").then((result) => {
      if (result) { setPending(rows(result.value)); setLoading(false); }
    }).catch((problem) => { setLoadError(errorMessage(problem)); setLoading(false); });
  }, [connection, refresh, run]);

  const submit = async (event: FormEvent, operation: Operation, path: string, makePayload: () => unknown, success: (value: Row) => void) => {
    event.preventDefault();
    if (!connection || blocked || mutationPending.current) return;
    setError(null); setNotice(null);
    let payload: unknown;
    try { payload = makePayload(); } catch (problem) { setError({ operation, message: errorMessage(problem) }); return; }
    mutationPending.current = true; setBusy(operation);
    try {
      const result = await run<Row>("mutation", connection, path, { method: "POST", body: JSON.stringify(payload) });
      if (result) { success(result.value); setRefresh((value) => value + 1); setBusy(null); }
    } catch (problem) { setError({ operation, message: errorMessage(problem) }); setBusy(null); }
    finally { mutationPending.current = false; }
  };
  const feedback = (operation: Operation) => <>{error?.operation === operation && <p className="notice error" role="alert">{error.message} 表单已保留，请核对后重试。</p>}{notice?.operation === operation && <p className="notice" role="status">{notice.message}</p>}</>;

  return <div className="settings-blocks trigger-settings">
    {!connection && <p className="notice">连接 Core 后可管理事件触发器。</p>}
    <SettingsSection title="待处理事件" description="显示最早的 32 条待处理事件；处理后会从此列表移除。">
      <div className="settings-form-content"><button className="button" type="button" disabled={!connection || loading} onClick={() => setRefresh((value) => value + 1)}>{loading ? "正在刷新…" : "刷新待处理事件"}</button>{loadError && <p className="notice error" role="alert">{loadError}</p>}{!loading && !loadError && !pending.length && <p className="notice">暂无待处理事件</p>}</div>
      {pending.map((item, index) => <TriggerRecord key={text(item.id, String(index))} item={item} />)}
    </SettingsSection>
    <SettingsSection title="事件源" description="先注册事件源，再接收事件、创建定时任务或文件监听。同一标识的类型与接收规则须保持一致。">
      <form className="settings-inline-form" onSubmit={(event) => void submit(event, "source", "/api/triggers/sources", () => sourceInput(source, kind, mode, actors), (value) => {
        setSources((current) => [...current.filter((item) => item.id !== value.id), value]);
        if (!eventSource) setEventSource(source.trim());
        if (kind === "timer") setTimerSource(source.trim());
        if (kind === "file") setFileSource(source.trim());
        setNotice({ operation: "source", message: `已注册事件源 ${text(value.id, source.trim())}` });
      })}>
        <fieldset className="settings-form-fieldset" disabled={disabled} aria-label="注册事件源">
        <label className="field-label">事件源标识<input value={source} required maxLength={256} onChange={(event) => setSource(event.target.value)} placeholder="例如 build-events" /></label>
        <label className="field-label">事件源类型<select aria-label="事件源类型" value={kind} onChange={(event) => setKind(event.target.value)}>{Object.entries(sourceKinds).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
        <label className="field-label">接收规则<select aria-label="接收规则" value={mode} onChange={(event) => setMode(event.target.value as AdmissionMode)}>{Object.entries(admissionModes).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
        <label className="field-label">允许的来源用户<textarea aria-label="允许的来源用户" rows={2} value={actors} onChange={(event) => setActors(event.target.value)} placeholder="每行一个标识；留空不限制来源用户" /></label>
        <div className="settings-form-content"><button type="submit" className="button" disabled={disabled}>{busy === "source" ? "正在注册…" : "注册事件源"}</button>{feedback("source")}</div>
        </fieldset>
      </form>
      {sources.length > 0 && <div className="settings-form-content"><p className="notice">本次打开设置后注册的事件源</p>{sources.map((item) => <div className="settings-result-record" key={text(item.id)}><strong>{text(item.id)}</strong><small>{sourceKinds[text(item.kind)] || text(item.kind)} · {admissionModes[text(item.mode) as AdmissionMode] || text(item.mode)}</small></div>)}</div>}
    </SettingsSection>
    <datalist id={sourceList}>{sources.map((item) => <option key={text(item.id)} value={text(item.id)}>{sourceKinds[text(item.kind)] || text(item.kind)}</option>)}</datalist>
    <SettingsSection title="接收事件" description="把外部事件交给已注册的事件源。相同事件标识可去重；失败时保留标识和内容。">
      <form className="settings-inline-form" onSubmit={(event) => void submit(event, "event", "/api/triggers", () => triggerInput(eventSource, externalId, thread, content, format, actor, addressed), (value) => { setEventReceipt(value); setNotice({ operation: "event", message: `事件已接收 · ${triggerStates[text(value.status)] || text(value.status)}` }); })}>
        <fieldset className="settings-form-fieldset" disabled={disabled} aria-label="接收事件">
        <label className="field-label">事件源标识<input required list={sourceList} maxLength={256} value={eventSource} onChange={(event) => setEventSource(event.target.value)} /></label>
        <label className="field-label">主题标识<input required maxLength={256} value={thread} onChange={(event) => setThread(event.target.value)} placeholder="同一话题使用相同标识" /></label>
        <ContentInput label="事件内容" value={content} onChange={setContent} format={format} onFormat={setFormat} />
        <details className="settings-disclosure settings-form-content"><summary>事件来源与去重</summary><div className="settings-disclosure-body"><label className="field-label">事件标识<input required maxLength={256} value={externalId} onChange={(event) => setExternalId(event.target.value)} /></label><label className="field-label">来源用户<input value={actor} maxLength={256} onChange={(event) => setActor(event.target.value)} /></label><label className="check-label"><input type="checkbox" checked={addressed} onChange={(event) => setAddressed(event.target.checked)} />此事件明确发给助手</label><button type="button" className="text-button" disabled={disabled} onClick={() => { setExternalId(`event_${crypto.randomUUID()}`); setEventReceipt(null); }}>生成新的事件标识</button></div></details>
        <div className="settings-form-content"><button className="button" type="submit" disabled={disabled}>{busy === "event" ? "正在接收…" : "接收此事件"}</button>{feedback("event")}</div>
        </fieldset>
      </form>{eventReceipt && <TriggerRecord item={eventReceipt} />}
    </SettingsSection>
    <SettingsSection title="定时任务" description="使用定时类型的事件源。执行时生成事件，再由 Router 或订阅成员处理。">
      <form className="settings-inline-form" onSubmit={(event) => void submit(event, "schedule", "/api/triggers/schedules", () => scheduleInput(timerSource, timerThread, timerContent, timerFormat, dueAt, interval), (value) => { setTimerReceipt(value); setNotice({ operation: "schedule", message: "定时任务已创建" }); })}>
        <fieldset className="settings-form-fieldset" disabled={disabled} aria-label="创建定时任务">
        <label className="field-label">定时事件源<input required list={sourceList} maxLength={256} value={timerSource} onChange={(event) => setTimerSource(event.target.value)} /></label>
        <label className="field-label">定时主题标识<input required maxLength={256} value={timerThread} onChange={(event) => setTimerThread(event.target.value)} /></label>
        <ContentInput label="定时任务内容" value={timerContent} onChange={setTimerContent} format={timerFormat} onFormat={setTimerFormat} />
        <label className="field-label">执行时间（本地）<input type="datetime-local" required value={dueAt} onChange={(event) => setDueAt(event.target.value)} /></label>
        <label className="field-label">重复间隔（秒）<input type="number" min="1" step="any" value={interval} onChange={(event) => setInterval(event.target.value)} placeholder="留空只执行一次" /></label>
        <div className="settings-form-content"><button className="button" type="submit" disabled={disabled}>{busy === "schedule" ? "正在创建…" : "创建定时任务"}</button>{feedback("schedule")}</div>
        </fieldset>
      </form>{timerReceipt && <div className="settings-result-record"><strong>本次创建的定时任务</strong><small className="break">{text(timerReceipt.id)}</small><dl><dt>下次执行</dt><dd>{typeof timerReceipt.next_at === "number" ? new Date(timerReceipt.next_at * 1000).toLocaleString() : "未记录"}</dd><dt>重复间隔</dt><dd>{typeof timerReceipt.interval_seconds === "number" ? `${timerReceipt.interval_seconds} 秒` : "仅执行一次"}</dd></dl></div>}
    </SettingsSection>
    <SettingsSection title="文件监听" description="使用文件类型的事件源，监听工作空间内的普通文件。文件变化会生成事件。">
      <form className="settings-inline-form" onSubmit={(event) => void submit(event, "watch", "/api/triggers/file-watches", () => fileWatchInput(fileSource, filePath, fileThread), (value) => { setWatchReceipt(value); setNotice({ operation: "watch", message: "文件监听已创建" }); })}>
        <fieldset className="settings-form-fieldset" disabled={disabled} aria-label="创建文件监听">
        <label className="field-label">文件事件源<input required list={sourceList} maxLength={256} value={fileSource} onChange={(event) => setFileSource(event.target.value)} /></label>
        <label className="field-label">相对文件路径<input required value={filePath} onChange={(event) => setFilePath(event.target.value)} placeholder="例如 reports/latest.txt" /></label>
        <label className="field-label">文件主题标识<input maxLength={256} value={fileThread} onChange={(event) => setFileThread(event.target.value)} placeholder="留空使用文件路径" /></label>
        <div className="settings-form-content"><button className="button" type="submit" disabled={disabled}>{busy === "watch" ? "正在创建…" : "创建文件监听"}</button>{feedback("watch")}</div>
        </fieldset>
      </form>{watchReceipt && <div className="settings-result-record"><strong className="break">{text(watchReceipt.relative_path)}</strong><small className="break">本次创建的监听 · {text(watchReceipt.id)}</small><small>主题：{text(watchReceipt.thread_key)}</small></div>}
    </SettingsSection>
  </div>;
}
