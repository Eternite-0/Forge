use crate::{credential_vault, protocol};
use serde::Serialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs,
    io::{self, Read, Write},
    net::{SocketAddr, TcpStream},
    os::windows::io::AsRawHandle,
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{mpsc, Arc, Mutex},
    thread,
    time::{Duration, Instant},
};
use windows_sys::Win32::{
    Foundation::{CloseHandle, GetLastError, ERROR_ALREADY_EXISTS, HANDLE},
    System::{
        JobObjects::{
            AssignProcessToJobObject, CreateJobObjectW, JobObjectBasicAccountingInformation,
            JobObjectExtendedLimitInformation, QueryInformationJobObject, SetInformationJobObject,
            TerminateJobObject, JOBOBJECT_BASIC_ACCOUNTING_INFORMATION,
            JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        },
        Threading::CreateMutexW,
    },
};

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}
struct Secret([u8; 32]);
impl Drop for Secret {
    fn drop(&mut self) {
        for byte in &mut self.0 {
            unsafe {
                std::ptr::write_volatile(byte, 0);
            }
        }
    }
}
struct SecretMap(BTreeMap<String, String>);
impl Drop for SecretMap {
    fn drop(&mut self) {
        for value in self.0.values_mut() {
            unsafe {
                for byte in value.as_bytes_mut() {
                    std::ptr::write_volatile(byte, 0);
                }
            }
        }
    }
}
fn hmac_sha256(key: &[u8], data: &[u8]) -> Vec<u8> {
    let mut inner = [0x36; 64];
    let mut outer = [0x5c; 64];
    for (i, byte) in key.iter().enumerate() {
        inner[i] ^= byte;
        outer[i] ^= byte;
    }
    let hash = Sha256::new()
        .chain_update(inner)
        .chain_update(data)
        .finalize();
    let result = Sha256::new()
        .chain_update(outer)
        .chain_update(hash)
        .finalize()
        .to_vec();
    for byte in inner.iter_mut().chain(outer.iter_mut()) {
        unsafe {
            std::ptr::write_volatile(byte, 0);
        }
    }
    result
}
pub fn workspace_root() -> io::Result<PathBuf> {
    let root = match std::env::var_os("FORGE_WORKSPACE_ROOT") {
        Some(value) => PathBuf::from(value),
        None => PathBuf::from(
            std::env::var_os("LOCALAPPDATA")
                .ok_or_else(|| io::Error::other("LOCALAPPDATA unavailable"))?,
        )
        .join("FORGE/workspaces/default"),
    };
    if !root.is_absolute() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "workspace must be absolute",
        ));
    }
    normalize_workspace(&root)
}
fn normalize_workspace(root: &Path) -> io::Result<PathBuf> {
    let parent = root
        .parent()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "workspace parent missing"))?;
    fs::create_dir_all(parent)?;
    let path = if root.exists() {
        fs::canonicalize(root)?
    } else {
        fs::canonicalize(parent)?.join(
            root.file_name()
                .ok_or_else(|| io::Error::other("workspace name missing"))?,
        )
    };
    // Python's Workspace identity uses ordinary Windows paths, not Win32's
    // verbatim prefix. Keep CLI and desktop bound to the same workspace ID.
    let value = path.to_str().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            "workspace path is not valid Unicode",
        )
    })?;
    if let Some(tail) = value.strip_prefix(r"\\?\UNC\") {
        Ok(PathBuf::from(format!(r"\\{tail}")))
    } else if let Some(tail) = value.strip_prefix(r"\\?\") {
        Ok(PathBuf::from(tail))
    } else {
        Ok(path)
    }
}
pub fn namespace(path: &Path) -> String {
    hex(&Sha256::digest(
        path.to_string_lossy().to_lowercase().as_bytes(),
    ))
}

struct OwnedHandle(HANDLE);
unsafe impl Send for OwnedHandle {}
unsafe impl Sync for OwnedHandle {}
impl Drop for OwnedHandle {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe {
                CloseHandle(self.0);
            }
        }
    }
}
fn wide(value: &str) -> Vec<u16> {
    value.encode_utf16().chain(Some(0)).collect()
}
fn app_mutex(namespace: &str) -> io::Result<OwnedHandle> {
    let name = wide(&format!("Local\\FORGE-Desktop-{namespace}"));
    let handle = unsafe { CreateMutexW(std::ptr::null(), 0, name.as_ptr()) };
    if handle.is_null() {
        return Err(io::Error::last_os_error());
    }
    let handle = OwnedHandle(handle);
    if unsafe { GetLastError() } == ERROR_ALREADY_EXISTS {
        return Err(io::Error::new(
            io::ErrorKind::AlreadyExists,
            "workspace desktop already running",
        ));
    }
    Ok(handle)
}
fn new_job(child: &Child) -> io::Result<OwnedHandle> {
    let raw = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
    if raw.is_null() {
        return Err(io::Error::last_os_error());
    }
    let job = OwnedHandle(raw);
    let mut info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    if unsafe {
        SetInformationJobObject(
            job.0,
            JobObjectExtendedLimitInformation,
            (&info as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
            std::mem::size_of_val(&info) as u32,
        )
    } == 0
        || unsafe { AssignProcessToJobObject(job.0, child.as_raw_handle().cast()) } == 0
    {
        return Err(io::Error::last_os_error());
    }
    Ok(job)
}
fn terminate_and_wait(job: &OwnedHandle) -> bool {
    if unsafe { TerminateJobObject(job.0, 1) } == 0 {
        return false;
    }
    let until = Instant::now() + Duration::from_secs(8);
    loop {
        let mut info = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION::default();
        let ok = unsafe {
            QueryInformationJobObject(
                job.0,
                JobObjectBasicAccountingInformation,
                (&mut info as *mut JOBOBJECT_BASIC_ACCOUNTING_INFORMATION).cast(),
                std::mem::size_of_val(&info) as u32,
                std::ptr::null_mut(),
            )
        };
        if ok == 0 {
            return false;
        }
        if info.ActiveProcesses == 0 {
            return true;
        }
        if Instant::now() >= until {
            return false;
        }
        thread::sleep(Duration::from_millis(50));
    }
}
fn request(port: u16, path: &str, bearer: Option<&str>) -> io::Result<Value> {
    let addr: SocketAddr = ([127, 0, 0, 1], port).into();
    let mut stream = TcpStream::connect_timeout(&addr, Duration::from_secs(2))?;
    stream.set_read_timeout(Some(Duration::from_secs(2)))?;
    let auth = bearer
        .map(|v| format!("Authorization: Bearer {v}\r\n"))
        .unwrap_or_default();
    write!(
        stream,
        "GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n{auth}Connection: close\r\n\r\n"
    )?;
    let mut body = Vec::new();
    stream.take(16385).read_to_end(&mut body)?;
    if body.len() > 16384 {
        return Err(io::Error::other("health response too large"));
    }
    let header_end = body
        .windows(4)
        .position(|w| w == b"\r\n\r\n")
        .ok_or_else(|| io::Error::other("invalid health response"))?;
    if !body.starts_with(b"HTTP/1.1 200 ") {
        return Err(io::Error::other("Core health rejected"));
    }
    serde_json::from_slice(&body[header_end + 4..]).map_err(io::Error::other)
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Connection {
    pub state: String,
    pub base_url: Option<String>,
    pub bearer: Option<String>,
    pub workspace_id: Option<String>,
    pub generation: Option<String>,
}
#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Status {
    pub state: String,
    pub generation: Option<String>,
    pub restart_count: u32,
    pub last_error_code: Option<String>,
}
struct Shared {
    connection: Connection,
    status: Status,
    operation: Option<Operation>,
}
#[derive(Clone, Serialize, serde::Deserialize)]
#[serde(rename_all = "camelCase")]
struct Operation {
    operation_id: String,
    operation: String,
    status: String,
    #[serde(default)]
    acknowledged: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    result: Option<Value>,
}
enum Control {
    Stop(mpsc::Sender<()>),
    Restart,
    Maintenance {
        operation: String,
        path: PathBuf,
        operation_id: String,
    },
}
pub struct CoreHost {
    shared: Arc<Mutex<Shared>>,
    control: mpsc::Sender<Control>,
    pub namespace: String,
    operation_dir: PathBuf,
    _app_mutex: OwnedHandle,
}
impl CoreHost {
    pub fn start(runtime: PathBuf) -> io::Result<Self> {
        Self::start_for_workspace(runtime, workspace_root()?)
    }
    fn start_for_workspace(runtime: PathBuf, root: PathBuf) -> io::Result<Self> {
        if !root.is_absolute() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "workspace must be absolute",
            ));
        }
        let root = normalize_workspace(&root)?;
        let ns = namespace(&root);
        let guard = app_mutex(&ns)?;
        let operation_dir = root
            .parent()
            .ok_or_else(|| io::Error::other("workspace parent missing"))?
            .join(format!(".forge-native-operations-{ns}"));
        fs::create_dir_all(&operation_dir)?;
        let operation = load_active_operation(&operation_dir)?;
        let shared = Arc::new(Mutex::new(Shared {
            connection: Connection {
                state: "starting".into(),
                base_url: None,
                bearer: None,
                workspace_id: None,
                generation: None,
            },
            status: Status {
                state: "starting".into(),
                generation: None,
                restart_count: 0,
                last_error_code: None,
            },
            operation,
        }));
        let (tx, rx) = mpsc::channel();
        let copy = shared.clone();
        let ns_copy = ns.clone();
        let operations_copy = operation_dir.clone();
        thread::spawn(move || supervise(runtime, root, ns_copy, operations_copy, copy, rx));
        Ok(Self {
            shared,
            control: tx,
            namespace: ns,
            operation_dir,
            _app_mutex: guard,
        })
    }
    pub fn connection(&self) -> Connection {
        self.shared.lock().unwrap().connection.clone()
    }
    pub fn status(&self) -> Status {
        self.shared.lock().unwrap().status.clone()
    }
    pub fn restart(&self) {
        let _ = self.control.send(Control::Restart);
    }
    pub fn stop(&self) {
        let (tx, rx) = mpsc::channel();
        if self.control.send(Control::Stop(tx)).is_ok() {
            let _ = rx.recv_timeout(Duration::from_secs(20));
        }
    }
    pub fn maintenance(&self, operation: &str, path: PathBuf) -> io::Result<Value> {
        if !path.is_absolute() || !["backup", "restore"].contains(&operation) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid maintenance request",
            ));
        }
        let mut state = self.shared.lock().unwrap();
        if state.status.state != "ready"
            || state.operation.as_ref().is_some_and(|op| {
                op.status != "completed" && !(op.status == "unknown" && op.acknowledged)
            })
        {
            return Err(io::Error::new(
                io::ErrorKind::WouldBlock,
                "BUSY_OR_UNRESOLVED",
            ));
        }
        let active = self.operation_dir.join("active.json");
        if active.exists() {
            fs::remove_file(&active)?;
        }
        let mut random = [0u8; 16];
        getrandom::fill(&mut random).map_err(|_| io::Error::other("secure random unavailable"))?;
        let record = Operation {
            operation_id: hex(&random),
            operation: operation.into(),
            status: "running".into(),
            acknowledged: false,
            result: None,
        };
        let temporary = self
            .operation_dir
            .join(format!("active-{}.tmp", record.operation_id));
        let mut file = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)?;
        serde_json::to_writer(&mut file, &record).map_err(io::Error::other)?;
        file.sync_all()?;
        drop(file);
        fs::rename(temporary, &active)?;
        state.operation = Some(record.clone());
        state.status.state = "maintenance".into();
        state.connection.state = "maintenance".into();
        state.connection.bearer = None;
        state.connection.base_url = None;
        if self
            .control
            .send(Control::Maintenance {
                operation: operation.into(),
                path,
                operation_id: record.operation_id.clone(),
            })
            .is_err()
        {
            if let Some(op) = state.operation.as_mut() {
                op.status = "unknown".into();
            }
            state.status.state = "failed".into();
            state.status.last_error_code = Some("SUPERVISOR_UNAVAILABLE".into());
            return Err(io::Error::other("supervisor unavailable"));
        }
        serde_json::to_value(record).map_err(io::Error::other)
    }
    pub fn operation_status(&self, id: Option<&str>) -> io::Result<Value> {
        let current = self.shared.lock().unwrap().operation.clone();
        if let Some(id) = id {
            if id.len() != 32 || !id.bytes().all(|b| b.is_ascii_hexdigit()) {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "invalid operation id",
                ));
            }
        }
        if let Some(op) = current {
            if id.is_none() || id == Some(op.operation_id.as_str()) {
                return serde_json::to_value(op).map_err(io::Error::other);
            }
        }
        let id =
            id.ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "operation not found"))?;
        let result = self.operation_dir.join(format!("result-{id}.json"));
        let file = fs::File::open(if result.exists() {
            result
        } else {
            self.operation_dir.join(format!("unknown-{id}.json"))
        })?;
        serde_json::from_reader(file).map_err(io::Error::other)
    }
    pub fn acknowledge_unknown(&self, id: &str, reason: &str) -> io::Result<Value> {
        if id.len() != 32
            || !id.bytes().all(|b| b.is_ascii_hexdigit())
            || reason.trim().is_empty()
            || reason.len() > 500
            || reason.chars().any(char::is_control)
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid acknowledgement",
            ));
        }
        let mut state = self.shared.lock().unwrap();
        if state.status.state != "ready" {
            return Err(io::Error::new(
                io::ErrorKind::WouldBlock,
                "Core is not ready",
            ));
        }
        let op = state
            .operation
            .as_mut()
            .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "operation not found"))?;
        if op.operation_id != id || op.status != "unknown" || op.acknowledged {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "operation is not unresolved",
            ));
        }
        let mut preserved = op.clone();
        preserved.acknowledged = true;
        write_new_record(
            &self.operation_dir.join(format!("unknown-{id}.json")),
            &serde_json::to_value(&preserved).map_err(io::Error::other)?,
        )?;
        let timestamp = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_err(io::Error::other)?
            .as_secs();
        write_new_record(
            &self.operation_dir.join(format!("ack-{id}.json")),
            &json!({"operationId":id,"actor":"local_user","reason":reason,"timestampUnix":timestamp,"outcome":"unknown"}),
        )?;
        *op = preserved;
        serde_json::to_value(op).map_err(io::Error::other)
    }
}
fn write_new_record(path: &Path, value: &Value) -> io::Result<()> {
    if path.exists() {
        let prior: Value =
            serde_json::from_reader(fs::File::open(path)?).map_err(io::Error::other)?;
        if prior == *value {
            return Ok(());
        }
        return Err(io::Error::new(
            io::ErrorKind::AlreadyExists,
            "record differs",
        ));
    }
    let mut nonce = [0u8; 8];
    getrandom::fill(&mut nonce).map_err(|_| io::Error::other("secure random unavailable"))?;
    let temp = path.with_extension(format!("{}.tmp", hex(&nonce)));
    let mut f = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temp)?;
    serde_json::to_writer(&mut f, value).map_err(io::Error::other)?;
    f.sync_all()?;
    drop(f);
    fs::rename(temp, path)
}
fn load_active_operation(dir: &Path) -> io::Result<Option<Operation>> {
    let path = dir.join("active.json");
    if !path.exists() {
        return Ok(None);
    }
    let mut op: Operation =
        serde_json::from_reader(fs::File::open(path)?).map_err(io::Error::other)?;
    if op.operation_id.len() != 32
        || !op.operation_id.bytes().all(|b| b.is_ascii_hexdigit())
        || !["backup", "restore"].contains(&op.operation.as_str())
    {
        return Err(io::Error::other("invalid maintenance record"));
    }
    let result = dir.join(format!("result-{}.json", op.operation_id));
    if result.exists() {
        op = serde_json::from_reader(fs::File::open(result)?).map_err(io::Error::other)?;
    } else {
        op.status = "unknown".into();
        op.result = None;
        let ack = dir.join(format!("ack-{}.json", op.operation_id));
        op.acknowledged = if ack.is_file() {
            let value: Value =
                serde_json::from_reader(fs::File::open(ack)?).map_err(io::Error::other)?;
            value.get("operationId").and_then(Value::as_str) == Some(op.operation_id.as_str())
                && value.get("outcome").and_then(Value::as_str) == Some("unknown")
                && value.get("actor").and_then(Value::as_str) == Some("local_user")
        } else {
            false
        };
    }
    Ok(Some(op))
}
fn finish_operation(dir: &Path, shared: &Arc<Mutex<Shared>>, id: &str, value: Value) {
    let mut state = shared.lock().unwrap();
    let Some(op) = state.operation.as_mut() else {
        return;
    };
    if op.operation_id != id {
        return;
    }
    let completed = Operation {
        status: "completed".into(),
        result: Some(value),
        ..op.clone()
    };
    let temp = dir.join(format!("result-{id}.tmp"));
    let final_path = dir.join(format!("result-{id}.json"));
    let saved = (|| -> io::Result<()> {
        let mut file = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temp)?;
        serde_json::to_writer(&mut file, &completed).map_err(io::Error::other)?;
        file.sync_all()?;
        fs::rename(temp, final_path)?;
        Ok(())
    })()
    .is_ok();
    if saved {
        *op = completed;
    } else {
        op.status = "unknown".into();
        op.result = None;
    }
}
fn set_state(shared: &Arc<Mutex<Shared>>, state: &str, error: Option<&str>, restarts: u32) {
    let mut data = shared.lock().unwrap();
    data.status.state = state.into();
    data.status.last_error_code = error.map(str::to_string);
    data.status.restart_count = restarts;
    data.connection.state = state.into();
    if state != "ready" {
        data.connection.base_url = None;
        data.connection.bearer = None;
        data.connection.generation = None;
        data.status.generation = None;
    }
}
fn restricted_environment(cmd: &mut Command) {
    const ALLOWED: &[&str] = &[
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "LOCALAPPDATA",
        "USERPROFILE",
        "PATH",
        "FORGE_ENV_FILE",
        "FORGE_DB_PATH",
        "FORGE_WORKSPACE_ID",
        "FORGE_PROVIDER_ID",
        "FORGE_PROVIDER_KIND",
        "FORGE_PROVIDER_BASE_URL",
        "FORGE_PROVIDER_CREDENTIAL_REF",
        "FORGE_PROVIDER_SUPPORTS_TOOLS",
        "FORGE_PROVIDER_SUPPORTS_JSON_SCHEMA",
        "FORGE_PROVIDER_ALLOW_INSECURE_LOOPBACK",
        "FORGE_ROUTER_MODEL_ID",
        "FORGE_ROUTER_CONTEXT_LIMIT",
        "FORGE_PROVIDERS_JSON",
        "FORGE_PRICE_BOUNDS_JSON",
        "FORGE_MODEL_TOKEN_LIMIT",
        "FORGE_MODEL_CONCURRENCY_LIMIT",
        "FORGE_MODEL_REMOTE_MAX_SECONDS",
        "FORGE_MODEL_SCOPE_LIMITS_JSON",
        "FORGE_MODEL_COST_LIMIT_MICROUNITS",
        "FORGE_MODEL_COST_CURRENCY",
        "FORGE_MODEL_HISTORICAL_COST_MICROUNITS",
        "FORGE_MODEL_SCOPE_COST_RECONCILIATIONS_JSON",
        "FORGE_TOOL_NETWORK_HOSTS_JSON",
        "FORGE_MCP_SERVERS_JSON",
    ];
    for (key, value) in std::env::vars_os() {
        if ALLOWED
            .iter()
            .any(|name| key.to_string_lossy().eq_ignore_ascii_case(name))
        {
            cmd.env(key, value);
        }
    }
}
fn wait_start_frame(
    rx: &mpsc::Receiver<io::Result<Value>>,
    controls: &mpsc::Receiver<Control>,
    child: &mut Child,
    operation_dir: &Path,
    shared: &Arc<Mutex<Shared>>,
) -> io::Result<Value> {
    let deadline = Instant::now() + Duration::from_secs(20);
    loop {
        match controls.try_recv() {
            Ok(Control::Stop(done)) => {
                let _ = child.kill();
                let _ = child.wait();
                let _ = done.send(());
                return Err(io::Error::other("STOP_REQUESTED"));
            }
            Ok(Control::Restart) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(io::Error::other("RESTART_REQUESTED"));
            }
            Ok(Control::Maintenance {
                operation,
                operation_id,
                ..
            }) => {
                finish_operation(
                    operation_dir,
                    shared,
                    &operation_id,
                    json!({"ok":false,"operation":operation,"code":"CORE_STARTING"}),
                );
            }
            Err(mpsc::TryRecvError::Disconnected) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(io::Error::other("STOP_REQUESTED"));
            }
            Err(mpsc::TryRecvError::Empty) => (),
        }
        if Instant::now() >= deadline {
            return Err(io::Error::other("Core handshake timeout"));
        }
        match rx.recv_timeout(Duration::from_millis(100)) {
            Ok(value) => return value,
            Err(mpsc::RecvTimeoutError::Timeout) => (),
            Err(_) => return Err(io::Error::other("Core handshake ended")),
        }
    }
}
fn spawn(
    runtime: &Path,
    root: &Path,
    ns: &str,
    controls: &mpsc::Receiver<Control>,
    operation_dir: &Path,
    shared: &Arc<Mutex<Shared>>,
) -> io::Result<(
    Child,
    OwnedHandle,
    mpsc::Receiver<io::Result<Value>>,
    Connection,
)> {
    let python = runtime.join("python.exe");
    if !python.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::NotFound,
            "packaged runtime missing",
        ));
    }
    let mut cmd = Command::new(python);
    cmd.args(["-I", "-m", "forge_api", "--desktop-host"])
        .current_dir(root.parent().unwrap_or(root))
        .env_clear()
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    restricted_environment(&mut cmd);
    let mut child = cmd.spawn()?;
    let job = match new_job(&child) {
        Ok(v) => v,
        Err(e) => {
            let _ = child.kill();
            let _ = child.wait();
            return Err(e);
        }
    };
    let mut secret = Secret([0u8; 32]);
    getrandom::fill(&mut secret.0).map_err(|_| io::Error::other("secure random unavailable"))?;
    let mut bootstrap = json!({"version":1,"type":"bootstrap","secret":hex(&secret.0),"workspace_root":root.to_string_lossy(),"credentials":{}});
    protocol::Bootstrap::validate(&bootstrap)?;
    let written = protocol::write_frame(
        child
            .stdin
            .as_mut()
            .ok_or_else(|| io::Error::other("stdin missing"))?,
        &bootstrap,
    );
    protocol::clear_values(&mut bootstrap);
    written?;
    let mut stdout = child
        .stdout
        .take()
        .ok_or_else(|| io::Error::other("stdout missing"))?;
    let (tx, rx) = mpsc::channel();
    thread::spawn(move || loop {
        let value = protocol::read_frame(&mut stdout);
        let done = value.is_err();
        if tx.send(value).is_err() || done {
            break;
        }
    });
    let required = wait_start_frame(&rx, controls, &mut child, operation_dir, shared)?;
    if required.get("type").and_then(Value::as_str) == Some("error") {
        return Err(io::Error::other(
            required
                .get("code")
                .and_then(Value::as_str)
                .unwrap_or("CORE_START_FAILED")
                .to_string(),
        ));
    }
    if required.get("version").and_then(Value::as_u64) != Some(1)
        || required.get("type").and_then(Value::as_str) != Some("credentials_required")
    {
        return Err(io::Error::other("invalid credential request"));
    }
    let providers = required
        .get("providers")
        .and_then(Value::as_array)
        .filter(|v| v.len() <= 17)
        .ok_or_else(|| io::Error::other("invalid credential request"))?;
    let mut credentials = SecretMap(BTreeMap::new());
    let mut refs = std::collections::HashSet::new();
    let mut identities = std::collections::HashSet::new();
    for item in providers {
        let provider = item
            .get("provider_id")
            .and_then(Value::as_str)
            .ok_or_else(|| io::Error::other("invalid provider"))?;
        let reference = item
            .get("credential_ref")
            .and_then(Value::as_str)
            .ok_or_else(|| io::Error::other("invalid reference"))?;
        if !refs.insert(reference.to_string()) || !identities.insert(provider.to_string()) {
            return Err(io::Error::other("duplicate provider credential"));
        }
        if let Some(value) = credential_vault::read_one(ns, provider, reference)? {
            credentials.0.insert(reference.to_string(), value);
        }
    }
    let mut credential_frame = json!({"version":1,"type":"credentials","values":&credentials.0});
    let written = protocol::write_frame(
        child
            .stdin
            .as_mut()
            .ok_or_else(|| io::Error::other("stdin missing"))?,
        &credential_frame,
    );
    protocol::clear_values(&mut credential_frame);
    drop(credentials);
    written?;
    let ready = wait_start_frame(&rx, controls, &mut child, operation_dir, shared)?;
    if ready.get("type").and_then(Value::as_str) == Some("error") {
        return Err(io::Error::other(
            ready
                .get("code")
                .and_then(Value::as_str)
                .unwrap_or("CORE_START_FAILED")
                .to_string(),
        ));
    }
    if ready.get("version").and_then(Value::as_u64) != Some(1)
        || ready.get("type").and_then(Value::as_str) != Some("ready")
        || ready.get("pid").and_then(Value::as_u64) != Some(child.id() as u64)
    {
        return Err(io::Error::other("invalid Core handshake"));
    }
    let port = ready
        .get("port")
        .and_then(Value::as_u64)
        .filter(|p| *p > 0 && *p <= 65535)
        .ok_or_else(|| io::Error::other("invalid Core port"))? as u16;
    let health = request(port, "/health", None)?;
    let challenge = health
        .get("session")
        .and_then(Value::as_str)
        .filter(|v| v.len() == 64 && v.bytes().all(|c| c.is_ascii_hexdigit()))
        .ok_or_else(|| io::Error::other("invalid Core challenge"))?;
    let token = hex(&hmac_sha256(&secret.0, challenge.as_bytes()));
    drop(secret);
    let verified = request(port, "/health", Some(&token))?;
    let workspace = ready
        .get("workspace_id")
        .and_then(Value::as_str)
        .filter(|v| !v.is_empty())
        .ok_or_else(|| io::Error::other("invalid workspace identity"))?;
    if verified.get("conversation_id").and_then(Value::as_str) != Some("main") {
        return Err(io::Error::other("Core authentication failed"));
    }
    let generation = ready
        .get("generation")
        .and_then(Value::as_str)
        .filter(|v| !v.is_empty())
        .ok_or_else(|| io::Error::other("invalid generation"))?;
    let connection = Connection {
        state: "ready".into(),
        base_url: Some(format!("http://127.0.0.1:{port}")),
        bearer: Some(token),
        workspace_id: Some(workspace.into()),
        generation: Some(generation.into()),
    };
    Ok((child, job, rx, connection))
}
fn stop_child(child: &mut Child, rx: &mpsc::Receiver<io::Result<Value>>) {
    if let Some(stdin) = child.stdin.as_mut() {
        let _ = protocol::write_frame(
            stdin,
            &json!({"version":1,"type":"shutdown","request_id":"desktop-exit"}),
        );
    }
    let _ = rx.recv_timeout(Duration::from_secs(5));
    drop(child.stdin.take());
    let until = Instant::now() + Duration::from_secs(3);
    while Instant::now() < until {
        if child.try_wait().ok().flatten().is_some() {
            return;
        }
        thread::sleep(Duration::from_millis(50));
    }
    let _ = child.kill();
    let _ = child.wait();
}
fn maintenance(runtime: &Path, root: &Path, operation: &str, path: &Path) -> Value {
    let mut command = Command::new(runtime.join("python.exe"));
    command
        .args([
            "-I",
            "-m",
            "forge_api.maintenance",
            operation,
            "--workspace-root",
        ])
        .arg(root)
        .arg("--path")
        .arg(path)
        .arg("--desktop-gated")
        .current_dir(root.parent().unwrap_or(root))
        .env_clear()
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    restricted_environment(&mut command);
    let failed = |code| json!({"ok":false,"operation":operation,"code":code});
    let mut child = match command.spawn() {
        Ok(c) => c,
        Err(_) => return failed("HELPER_SPAWN_FAILED"),
    };
    let _job = match new_job(&child) {
        Ok(job) => job,
        Err(_) => {
            let _ = child.kill();
            let _ = child.wait();
            return failed("HELPER_JOB_FAILED");
        }
    };
    let mut gate = match child.stdin.take() {
        Some(value) => value,
        None => return failed("HELPER_GATE_FAILED"),
    };
    if gate.write_all(b"GO1\n").is_err() {
        return failed("HELPER_GATE_FAILED");
    }
    drop(gate);
    let mut stdout = match child.stdout.take() {
        Some(out) => out,
        None => return failed("HELPER_OUTPUT_FAILED"),
    };
    let (tx, rx) = mpsc::channel();
    thread::spawn(move || {
        let mut data = Vec::new();
        let _ = stdout.by_ref().take(65537).read_to_end(&mut data);
        let _ = tx.send(data);
    });
    let status = match child.wait() {
        Ok(status) => status,
        Err(_) => return failed("HELPER_WAIT_FAILED"),
    };
    let data = match rx.recv_timeout(Duration::from_secs(1)) {
        Ok(d) if d.len() <= 65536 => d,
        _ => return failed("HELPER_OUTPUT_FAILED"),
    };
    let value: Value = match serde_json::from_slice(&data) {
        Ok(v) => v,
        Err(_) => return failed("HELPER_OUTPUT_INVALID"),
    };
    let ok = value.get("ok").and_then(Value::as_bool);
    let code = value.get("code").and_then(Value::as_str);
    if ok == Some(true) && status.success() {
        json!({"ok":true,"operation":operation})
    } else if ok == Some(false)
        && matches!(
            code,
            Some("WORKSPACE_BUSY" | "INVALID_BACKUP_OR_WORKSPACE" | "MAINTENANCE_IO_ERROR")
        )
    {
        json!({"ok":false,"operation":operation,"code":code})
    } else {
        failed("MAINTENANCE_IO_ERROR")
    }
}
fn supervise(
    runtime: PathBuf,
    root: PathBuf,
    ns: String,
    operation_dir: PathBuf,
    shared: Arc<Mutex<Shared>>,
    controls: mpsc::Receiver<Control>,
) {
    let mut restarts = 0;
    loop {
        set_state(&shared, "starting", None, restarts);
        let (mut child, job, rx, connection) =
            match spawn(&runtime, &root, &ns, &controls, &operation_dir, &shared) {
                Ok(v) => v,
                Err(e) => {
                    if e.to_string() == "STOP_REQUESTED" {
                        set_state(&shared, "stopped", None, restarts);
                        return;
                    }
                    if e.to_string() == "RESTART_REQUESTED" {
                        continue;
                    }
                    let code = if e.to_string().contains("WORKSPACE_BUSY") {
                        "WORKSPACE_BUSY"
                    } else {
                        "CORE_START_FAILED"
                    };
                    set_state(&shared, "failed", Some(code), restarts);
                    match controls.recv() {
                        Ok(Control::Restart) => (),
                        Ok(Control::Stop(done)) => {
                            set_state(&shared, "stopped", None, restarts);
                            let _ = done.send(());
                            return;
                        }
                        Ok(Control::Maintenance {
                            operation,
                            path,
                            operation_id,
                        }) => {
                            let value = maintenance(&runtime, &root, &operation, &path);
                            finish_operation(&operation_dir, &shared, &operation_id, value);
                        }
                        Err(_) => return,
                    }
                    continue;
                }
            };
        {
            let mut s = shared.lock().unwrap();
            s.status.state = "ready".into();
            s.status.generation = connection.generation.clone();
            s.connection = connection;
        }
        let mut intentional = false;
        let mut job_cleaned = false;
        let mut last_health = Instant::now();
        let mut health_failures = 0;
        loop {
            match controls.recv_timeout(Duration::from_millis(250)) {
                Ok(Control::Stop(done)) => {
                    set_state(&shared, "stopping", None, restarts);
                    stop_child(&mut child, &rx);
                    let _ = terminate_and_wait(&job);
                    set_state(&shared, "stopped", None, restarts);
                    let _ = done.send(());
                    return;
                }
                Ok(Control::Restart) => {
                    set_state(&shared, "restarting", None, restarts);
                    stop_child(&mut child, &rx);
                    intentional = true;
                    break;
                }
                Ok(Control::Maintenance {
                    operation,
                    path,
                    operation_id,
                }) => {
                    set_state(&shared, "maintenance", None, restarts);
                    stop_child(&mut child, &rx);
                    if !terminate_and_wait(&job) {
                        set_state(&shared, "failed", Some("CORE_TREE_STILL_RUNNING"), restarts);
                        finish_operation(
                            &operation_dir,
                            &shared,
                            &operation_id,
                            json!({"ok":false,"operation":operation,"code":"CORE_TREE_STILL_RUNNING"}),
                        );
                        return;
                    }
                    job_cleaned = true;
                    let value = maintenance(&runtime, &root, &operation, &path);
                    finish_operation(&operation_dir, &shared, &operation_id, value);
                    intentional = true;
                    break;
                }
                Err(mpsc::RecvTimeoutError::Disconnected) => {
                    stop_child(&mut child, &rx);
                    return;
                }
                Err(mpsc::RecvTimeoutError::Timeout) => (),
            }
            if child.try_wait().ok().flatten().is_some() {
                break;
            }
            if last_health.elapsed() >= Duration::from_secs(5) {
                last_health = Instant::now();
                let conn = shared.lock().unwrap().connection.clone();
                let healthy = conn
                    .base_url
                    .as_deref()
                    .and_then(|url| url.rsplit(':').next())
                    .and_then(|port| port.parse::<u16>().ok())
                    .and_then(|port| request(port, "/health", conn.bearer.as_deref()).ok())
                    .and_then(|v| {
                        v.get("conversation_id")
                            .and_then(Value::as_str)
                            .map(str::to_string)
                    })
                    .as_deref()
                    == Some("main");
                if healthy {
                    health_failures = 0;
                } else {
                    health_failures += 1;
                }
                if health_failures >= 3 {
                    stop_child(&mut child, &rx);
                    break;
                }
            }
        }
        if !job_cleaned && !terminate_and_wait(&job) {
            set_state(&shared, "failed", Some("CORE_TREE_STILL_RUNNING"), restarts);
            return;
        }
        if !intentional {
            restarts += 1;
            if restarts > 3 {
                set_state(&shared, "failed", Some("CORE_CRASH_LIMIT"), restarts);
                loop {
                    match controls.recv() {
                        Ok(Control::Restart) => {
                            restarts = 0;
                            break;
                        }
                        Ok(Control::Stop(done)) => {
                            let _ = done.send(());
                            return;
                        }
                        Ok(Control::Maintenance {
                            operation,
                            path,
                            operation_id,
                        }) => {
                            finish_operation(
                                &operation_dir,
                                &shared,
                                &operation_id,
                                maintenance(&runtime, &root, &operation, &path),
                            );
                        }
                        Err(_) => return,
                    }
                }
            } else {
                thread::sleep(Duration::from_millis(250 * (1 << (restarts - 1))));
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hmac_matches_rfc4231() {
        assert_eq!(
            hex(&hmac_sha256(&[0x0b; 20], b"Hi There")),
            "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7"
        );
    }

    #[test]
    fn owned_job_termination_waits_for_active_processes() {
        let system_root = std::env::var_os("SystemRoot").expect("Windows system root");
        let mut child = Command::new(PathBuf::from(system_root).join("System32/ping.exe"))
            .args(["-n", "30", "127.0.0.1"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .unwrap();
        let job = new_job(&child).unwrap();
        assert!(terminate_and_wait(&job));
        assert!(child.wait().is_ok());
    }

    #[test]
    #[ignore = "requires packaged runtime and isolated root"]
    fn owned_job_terminates_descendant_process() {
        let runtime =
            std::env::var_os("FORGE_NATIVE_TEST_RUNTIME").expect("packaged runtime required");
        let program="import subprocess,sys,time; sys.stdin.buffer.read(1); p=subprocess.Popen([sys.executable,'-I','-c','import time; time.sleep(60)']); print(p.pid,flush=True); time.sleep(60)";
        let mut child = Command::new(PathBuf::from(runtime).join("python.exe"))
            .args(["-I", "-c", program])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .unwrap();
        let job = new_job(&child).unwrap();
        child.stdin.take().unwrap().write_all(b"x").unwrap();
        let mut line = String::new();
        use std::io::BufRead;
        io::BufReader::new(child.stdout.take().unwrap())
            .read_line(&mut line)
            .unwrap();
        assert!(line.trim().parse::<u32>().unwrap() > 0);
        assert!(terminate_and_wait(&job));
        assert!(child.wait().is_ok());
    }

    #[test]
    fn normalization_does_not_create_missing_workspace() {
        let parent =
            std::env::temp_dir().join(format!("forge-native-normalize-{}", std::process::id()));
        fs::create_dir_all(&parent).unwrap();
        let root = parent.join("missing-workspace");
        let normalized = normalize_workspace(&root).unwrap();
        assert!(!root.exists());
        assert_eq!(normalized.file_name().unwrap(), "missing-workspace");
    }

    #[test]
    fn interrupted_maintenance_remains_unknown_after_restart() {
        let mut random = [0u8; 8];
        getrandom::fill(&mut random).unwrap();
        let parent = std::env::temp_dir().join(format!("forge-native-op-{}", hex(&random)));
        fs::create_dir_all(&parent).unwrap();
        let op = Operation {
            operation_id: "a".repeat(32),
            operation: "restore".into(),
            status: "running".into(),
            acknowledged: false,
            result: None,
        };
        let active = parent.join("active.json");
        fs::write(&active, serde_json::to_vec(&op).unwrap()).unwrap();
        let loaded = load_active_operation(&parent).unwrap().unwrap();
        assert_eq!(loaded.status, "unknown");
        assert!(loaded.result.is_none());
        let completed = Operation {
            status: "completed".into(),
            result: Some(json!({"ok":true,"operation":"restore"})),
            ..op
        };
        fs::write(
            parent.join(format!("result-{}.json", completed.operation_id)),
            serde_json::to_vec(&completed).unwrap(),
        )
        .unwrap();
        assert_eq!(
            load_active_operation(&parent).unwrap().unwrap().status,
            "completed"
        );
    }

    #[test]
    fn acknowledgement_preserves_unknown_outcome_and_audit() {
        let mut random = [0u8; 16];
        getrandom::fill(&mut random).unwrap();
        let ns = hex(&Sha256::digest(random));
        let dir = std::env::temp_dir().join(format!("forge-ack-{}", hex(&random)));
        fs::create_dir_all(&dir).unwrap();
        let op = Operation {
            operation_id: hex(&random),
            operation: "restore".into(),
            status: "unknown".into(),
            acknowledged: false,
            result: None,
        };
        fs::write(dir.join("active.json"), serde_json::to_vec(&op).unwrap()).unwrap();
        let shared = Arc::new(Mutex::new(Shared {
            connection: Connection {
                state: "ready".into(),
                base_url: None,
                bearer: None,
                workspace_id: None,
                generation: None,
            },
            status: Status {
                state: "ready".into(),
                generation: None,
                restart_count: 0,
                last_error_code: None,
            },
            operation: Some(op.clone()),
        }));
        let (tx, _rx) = mpsc::channel();
        let host = CoreHost {
            shared,
            control: tx,
            namespace: ns.clone(),
            operation_dir: dir.clone(),
            _app_mutex: app_mutex(&ns).unwrap(),
        };
        assert!(host.acknowledge_unknown(&op.operation_id, "").is_err());
        let acknowledged = host
            .acknowledge_unknown(&op.operation_id, "inspected workspace and backup")
            .unwrap();
        assert_eq!(acknowledged["status"], "unknown");
        assert_eq!(acknowledged["acknowledged"], true);
        assert!(dir.join(format!("ack-{}.json", op.operation_id)).is_file());
        let after_restart = load_active_operation(&dir).unwrap().unwrap();
        assert_eq!(after_restart.status, "unknown");
        assert!(after_restart.acknowledged);
    }

    #[test]
    #[ignore = "requires packaged runtime and isolated root"]
    fn isolated_embedded_core_handshake_restart_and_exit() {
        let runtime =
            std::env::var_os("FORGE_NATIVE_TEST_RUNTIME").expect("packaged runtime required");
        let base = PathBuf::from(
            std::env::var_os("FORGE_NATIVE_TEST_ROOT").expect("isolated test root required"),
        );
        assert!(base.is_absolute());
        let root = base.join(format!("native-lifecycle-{}", std::process::id()));
        assert!(!root.exists());
        let runtime = PathBuf::from(runtime);
        let host = CoreHost::start_for_workspace(runtime.clone(), root.clone()).unwrap();
        let deadline = Instant::now() + Duration::from_secs(30);
        while host.status().state != "ready" && Instant::now() < deadline {
            if host.status().state == "failed" {
                panic!("startup failed: {:?}", host.status().last_error_code);
            }
            thread::sleep(Duration::from_millis(100));
        }
        assert_eq!(host.status().state, "ready");
        let first = host.connection();
        let expected=Command::new(runtime.join("python.exe")).args(["-I","-c","from forge_core.settings import Workspace; import sys; print(Workspace.from_path(sys.argv[1]).id)"]).arg(&root).output().unwrap();
        assert!(expected.status.success());
        assert_eq!(
            first.workspace_id.as_deref(),
            Some(String::from_utf8(expected.stdout).unwrap().trim())
        );
        assert!(first.bearer.as_ref().is_some_and(|v| v.len() == 64));
        assert!(first
            .base_url
            .as_ref()
            .is_some_and(|v| v.starts_with("http://127.0.0.1:")));
        assert!(CoreHost::start_for_workspace(PathBuf::new(), root).is_err());
        host.restart();
        let deadline = Instant::now() + Duration::from_secs(30);
        while (host.status().state != "ready" || host.connection().generation == first.generation)
            && Instant::now() < deadline
        {
            thread::sleep(Duration::from_millis(100));
        }
        let second = host.connection();
        assert_eq!(second.state, "ready");
        assert_ne!(second.generation, first.generation);
        assert_ne!(second.bearer, first.bearer);
        let backup = base.join(format!("native-backup-{}", std::process::id()));
        assert!(!backup.exists());
        let accepted = host.maintenance("backup", backup.clone()).unwrap();
        let op_id = accepted["operationId"].as_str().unwrap().to_string();
        assert!(host
            .maintenance("backup", base.join("duplicate-backup"))
            .is_err());
        let deadline = Instant::now() + Duration::from_secs(60);
        loop {
            let operation = host.operation_status(Some(&op_id)).unwrap();
            if operation["status"] == "completed" {
                assert_eq!(
                    operation["result"]["ok"], true,
                    "sanitized result: {}",
                    operation["result"]
                );
                break;
            }
            assert!(Instant::now() < deadline, "backup did not complete");
            thread::sleep(Duration::from_millis(100));
        }
        let deadline = Instant::now() + Duration::from_secs(30);
        while (host.status().state != "ready" || host.connection().generation == second.generation)
            && Instant::now() < deadline
        {
            thread::sleep(Duration::from_millis(100));
        }
        assert_eq!(host.status().state, "ready");
        let saved = host.operation_status(Some(&op_id)).unwrap();
        assert_eq!(saved["result"]["operation"], "backup");
        let prior_generation = host.connection().generation;
        let restored = host.maintenance("restore", backup).unwrap();
        let restore_id = restored["operationId"].as_str().unwrap();
        let deadline = Instant::now() + Duration::from_secs(60);
        loop {
            let operation = host.operation_status(Some(restore_id)).unwrap();
            if operation["status"] == "completed" {
                assert_eq!(
                    operation["result"]["ok"], true,
                    "restore result: {}",
                    operation["result"]
                );
                break;
            }
            assert!(Instant::now() < deadline, "restore did not complete");
            thread::sleep(Duration::from_millis(100));
        }
        let deadline = Instant::now() + Duration::from_secs(30);
        while (host.status().state != "ready" || host.connection().generation == prior_generation)
            && Instant::now() < deadline
        {
            thread::sleep(Duration::from_millis(100));
        }
        let restored_connection = host.connection();
        assert_eq!(restored_connection.state, "ready");
        let port = restored_connection
            .base_url
            .as_deref()
            .unwrap()
            .rsplit(':')
            .next()
            .unwrap()
            .parse::<u16>()
            .unwrap();
        let mut stream = TcpStream::connect(("127.0.0.1", port)).unwrap();
        stream
            .set_read_timeout(Some(Duration::from_secs(2)))
            .unwrap();
        write!(stream,"POST /api/conversation/message HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAuthorization: Bearer {}\r\nContent-Type: application/json\r\nContent-Length: 15\r\nConnection: close\r\n\r\n{{\"content\":\"x\"}}",restored_connection.bearer.as_deref().unwrap()).unwrap();
        let mut response = String::new();
        stream.read_to_string(&mut response).unwrap();
        assert!(
            response.starts_with("HTTP/1.1 423 "),
            "restore fence did not block write"
        );
        host.stop();
        assert_eq!(host.status().state, "stopped");
        assert!(host.connection().bearer.is_none());
    }
}
