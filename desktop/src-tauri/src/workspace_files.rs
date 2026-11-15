//! Bounded, read-only file inspection scoped to the Core host's immutable workspace root.
use serde::Serialize;
use std::fs::{self, File};
use std::io::Read;
use std::path::{Component, Path, PathBuf};
use std::process::{Command, Output, Stdio};
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant, UNIX_EPOCH};

const FILE_LIMIT: u64 = 24 * 1024 * 1024;
const TEXT_LIMIT: u64 = 2 * 1024 * 1024;
const GIT_LIMIT: usize = 4 * 1024 * 1024;
const ENTRY_LIMIT: usize = 5000;

#[derive(Serialize)]
pub struct WorkspaceFile {
    path: String,
    name: String,
    directory: bool,
    size: u64,
    modified: Option<u64>,
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct FileChange {
    path: String,
    status: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    previous_path: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    added: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    removed: Option<u64>,
}
#[derive(Serialize)]
pub struct ChangeSet {
    files: Vec<FileChange>,
    repository: bool,
    truncated: bool,
}
#[derive(Serialize)]
pub struct WorkspaceDocument {
    path: String,
    kind: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    text: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    bytes: Option<Vec<u8>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    mime: Option<String>,
    size: u64,
    modified: Option<u64>,
}
#[derive(Serialize)]
pub struct FileDiff {
    text: String,
    binary: bool,
    truncated: bool,
    scope: String,
}

fn relative(value: &str, allow_empty: bool) -> Result<PathBuf, String> {
    if value.is_empty() && allow_empty {
        return Ok(PathBuf::new());
    }
    if value.is_empty() || value.len() > 4096 || value.chars().any(|c| c.is_control() || c == ':') {
        return Err("文件路径不在当前工作空间中。".into());
    }
    let normalized = value.replace('\\', "/");
    if normalized
        .split('/')
        .any(|part| part.is_empty() || part == "." || part == "..")
    {
        return Err("文件路径不在当前工作空间中。".into());
    }
    let path = PathBuf::from(normalized);
    if path
        .components()
        .any(|part| !matches!(part, Component::Normal(_)))
    {
        return Err("文件路径不在当前工作空间中。".into());
    }
    Ok(path)
}

fn root_path(root: &Path) -> Result<PathBuf, String> {
    fs::canonicalize(root).map_err(|_| "当前工作空间目录不可用。".into())
}

fn is_link(metadata: &fs::Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        metadata.file_attributes() & 0x400 != 0
    }
    #[cfg(not(windows))]
    metadata.file_type().is_symlink()
}

fn resolve(
    root: &Path,
    value: &str,
    allow_empty: bool,
    allow_missing: bool,
) -> Result<PathBuf, String> {
    let relative = relative(value, allow_empty)?;
    let canonical_root = root_path(root)?;
    let mut candidate = canonical_root.clone();
    for part in relative.components() {
        candidate.push(part.as_os_str());
        match fs::symlink_metadata(&candidate) {
            Ok(metadata) if is_link(&metadata) => {
                return Err("不能预览符号链接或重解析目录。".into())
            }
            Ok(_) => {}
            Err(error) if allow_missing && error.kind() == std::io::ErrorKind::NotFound => {}
            Err(_) => return Err("文件或目录不可用。".into()),
        }
    }
    let existing = if candidate.exists() {
        candidate.as_path()
    } else {
        candidate
            .ancestors()
            .find(|path| path.exists())
            .ok_or("文件路径不可用。")?
    };
    if !fs::canonicalize(existing)
        .map_err(|_| "文件路径不可用。")?
        .starts_with(&canonical_root)
    {
        return Err("文件路径不在当前工作空间中。".into());
    }
    Ok(candidate)
}

fn modified(metadata: &fs::Metadata) -> Option<u64> {
    metadata
        .modified()
        .ok()?
        .duration_since(UNIX_EPOCH)
        .ok()
        .map(|value| value.as_millis() as u64)
}

fn path_text(path: &Path) -> Result<String, String> {
    path.to_str()
        .map(|value| value.replace('\\', "/"))
        .ok_or("此文件名不是有效 Unicode。".into())
}

pub fn list(root: &Path, path: &str) -> Result<Vec<WorkspaceFile>, String> {
    let directory = resolve(root, path, true, false)?;
    if !directory.is_dir() {
        return Err("此路径不是目录。".into());
    }
    let mut files = Vec::new();
    let canonical_root = root_path(root)?;
    for entry in fs::read_dir(directory).map_err(|_| "无法读取工作空间目录。")? {
        let entry = entry.map_err(|_| "无法读取目录项目。")?;
        let metadata = fs::symlink_metadata(entry.path()).map_err(|_| "无法读取文件信息。")?;
        if is_link(&metadata) {
            continue;
        }
        if files.len() >= ENTRY_LIMIT {
            return Err("此目录超过 5000 项，请打开更具体的子目录。".into());
        }
        let relative = path_text(
            entry
                .path()
                .strip_prefix(&canonical_root)
                .map_err(|_| "目录越过工作空间边界。")?,
        )?;
        files.push(WorkspaceFile {
            path: relative,
            name: entry.file_name().to_string_lossy().into_owned(),
            directory: metadata.is_dir(),
            size: metadata.len(),
            modified: modified(&metadata),
        });
    }
    files.sort_by(|a, b| {
        b.directory
            .cmp(&a.directory)
            .then_with(|| a.name.to_lowercase().cmp(&b.name.to_lowercase()))
    });
    Ok(files)
}

fn opened_in_workspace(file: &File, root: &Path) -> Result<(), String> {
    #[cfg(windows)]
    {
        use std::ffi::OsString;
        use std::os::windows::ffi::OsStringExt;
        use std::os::windows::io::AsRawHandle;
        #[link(name = "kernel32")]
        extern "system" {
            fn GetFinalPathNameByHandleW(
                handle: *mut std::ffi::c_void,
                path: *mut u16,
                size: u32,
                flags: u32,
            ) -> u32;
        }
        let mut buffer = vec![0_u16; 32768];
        let length = unsafe {
            GetFinalPathNameByHandleW(
                file.as_raw_handle(),
                buffer.as_mut_ptr(),
                buffer.len() as u32,
                0,
            )
        } as usize;
        if length == 0 || length >= buffer.len() {
            return Err("无法验证文件的工作空间范围。".into());
        }
        let final_path = PathBuf::from(OsString::from_wide(&buffer[..length]));
        if !final_path.starts_with(root_path(root)?) {
            return Err("文件路径不在当前工作空间中。".into());
        }
    }
    #[cfg(not(windows))]
    {
        let _ = (file, root);
    }
    Ok(())
}

pub fn read(root: &Path, path: &str) -> Result<WorkspaceDocument, String> {
    let resolved = resolve(root, path, false, false)?;
    let mut file = File::open(&resolved).map_err(|_| "无法打开文件。")?;
    opened_in_workspace(&file, root)?;
    let metadata = file.metadata().map_err(|_| "无法读取文件信息。")?;
    if !metadata.is_file() {
        return Err("此路径不是普通文件。".into());
    }
    if metadata.len() > FILE_LIMIT {
        return Err("文件超过 24 MB 预览上限。".into());
    }
    let extension = resolved
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    let mime = match extension.as_str() {
        "pdf" => Some("application/pdf"),
        "png" => Some("image/png"),
        "jpg" | "jpeg" => Some("image/jpeg"),
        "gif" => Some("image/gif"),
        "webp" => Some("image/webp"),
        "avif" => Some("image/avif"),
        "bmp" => Some("image/bmp"),
        _ => None,
    };
    let unsupported = matches!(
        extension.as_str(),
        "zip"
            | "exe"
            | "dll"
            | "db"
            | "sqlite"
            | "sqlite3"
            | "woff"
            | "woff2"
            | "ttf"
            | "mp4"
            | "mp3"
            | "doc"
            | "docx"
            | "xls"
            | "xlsx"
            | "ppt"
            | "pptx"
            | "bin"
            | "ico"
    );
    let mut document = WorkspaceDocument {
        path: path_text(&relative(path, false)?)?,
        kind: "unsupported".into(),
        text: None,
        bytes: None,
        mime: mime.map(str::to_string),
        size: metadata.len(),
        modified: modified(&metadata),
    };
    if unsupported {
        return Ok(document);
    }
    let limit = if mime.is_some() {
        FILE_LIMIT
    } else {
        TEXT_LIMIT
    };
    if metadata.len() > limit {
        return Err("文本超过 2 MB 预览上限。".into());
    }
    let mut bytes = Vec::with_capacity(metadata.len() as usize);
    file.by_ref()
        .take(limit + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| "无法读取文件内容。")?;
    if bytes.len() as u64 > limit {
        return Err("文件在读取期间超过预览上限。".into());
    }
    if mime.is_some() {
        document.kind = if extension == "pdf" { "pdf" } else { "image" }.into();
        document.bytes = Some(bytes);
    } else if !bytes.contains(&0) {
        document.text =
            Some(String::from_utf8(bytes).map_err(|_| "此文件不是 UTF-8 文本，暂不支持预览。")?);
        document.kind = "text".into();
    }
    Ok(document)
}

fn git(root: &Path, args: &[&str]) -> Result<Output, String> {
    let mut command = Command::new("git");
    command
        .args([
            "--no-pager",
            "--literal-pathspecs",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.quotePath=false",
            "-c",
            "core.hooksPath=",
            "-C",
        ])
        .arg(root)
        .args(args)
        .env_clear()
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    for key in ["SystemRoot", "PATH", "TEMP", "TMP"] {
        if let Some(value) = std::env::var_os(key) {
            command.env(key, value);
        }
    }
    command
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .env(
            "GIT_CONFIG_GLOBAL",
            if cfg!(windows) { "NUL" } else { "/dev/null" },
        )
        .env("GIT_OPTIONAL_LOCKS", "0")
        .env("GIT_TERMINAL_PROMPT", "0");
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    let mut child = command.spawn().map_err(|_| "Git 不可用，无法读取更改。")?;
    let stdout = child.stdout.take().ok_or("无法读取 Git 输出。")?;
    let stderr = child.stderr.take().ok_or("无法读取 Git 输出。")?;
    let (tx, rx) = mpsc::channel();
    let output_reader = thread::spawn(move || {
        let mut bytes = Vec::new();
        let result = stdout.take(GIT_LIMIT as u64 + 1).read_to_end(&mut bytes);
        let _ = tx.send((result, bytes));
    });
    let error_reader = thread::spawn(move || {
        let mut bytes = Vec::new();
        let mut stderr = stderr;
        let mut chunk = [0_u8; 8192];
        while let Ok(length) = stderr.read(&mut chunk) {
            if length == 0 {
                break;
            }
            let retained = length.min(65536_usize.saturating_sub(bytes.len()));
            bytes.extend_from_slice(&chunk[..retained]);
        }
        bytes
    });
    let deadline = Instant::now() + Duration::from_secs(8);
    let mut captured = None;
    let mut failure = None;
    let status = loop {
        if let Ok((result, bytes)) = rx.try_recv() {
            if result.is_err() {
                failure = Some("无法读取 Git 输出。");
            }
            if bytes.len() > GIT_LIMIT {
                failure = Some("更改超过 4 MB 读取上限，请缩小范围。");
            }
            captured = Some(bytes);
        }
        if failure.is_some() || Instant::now() >= deadline {
            let _ = child.kill();
            if failure.is_none() {
                failure = Some("Git 读取超时，请稍后重试。");
            }
            break child.wait().map_err(|_| "无法结束 Git 读取。")?;
        }
        if let Some(status) = child.try_wait().map_err(|_| "无法查询 Git 读取状态。")? {
            break status;
        }
        thread::sleep(Duration::from_millis(10));
    };
    let _ = output_reader.join();
    let stderr = error_reader.join().unwrap_or_default();
    let stdout = captured
        .or_else(|| rx.recv().ok().map(|(_, bytes)| bytes))
        .unwrap_or_default();
    if stdout.len() > GIT_LIMIT {
        failure = Some("更改超过 4 MB 读取上限，请缩小范围。");
    }
    if let Some(message) = failure {
        return Err(message.into());
    }
    Ok(Output {
        status,
        stdout,
        stderr,
    })
}

fn repository_prefix(root: &Path) -> Result<Option<String>, String> {
    let output = git(root, &["rev-parse", "--show-toplevel"])?;
    if !output.status.success() {
        return Ok(None);
    }
    let value = String::from_utf8(output.stdout).map_err(|_| "Git 路径不是 UTF-8。")?;
    let repository = fs::canonicalize(value.trim()).map_err(|_| "Git 根目录不可用。")?;
    let workspace = root_path(root)?;
    let prefix = workspace
        .strip_prefix(repository)
        .map_err(|_| "Git 目录越过工作空间边界。")?;
    Ok(Some(path_text(prefix)?))
}

fn scoped_git_path(prefix: &str, path: &str) -> Option<String> {
    let path = if prefix.is_empty() {
        path
    } else {
        path.strip_prefix(prefix)?.strip_prefix('/')?
    };
    relative(path, false).ok()?;
    Some(path.to_string())
}

pub fn changes(root: &Path) -> Result<ChangeSet, String> {
    let Some(prefix) = repository_prefix(root)? else {
        return Ok(ChangeSet {
            files: Vec::new(),
            repository: false,
            truncated: false,
        });
    };
    let output = git(
        root,
        &[
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--ignore-submodules=all",
            "--",
            ".",
        ],
    )?;
    if !output.status.success() {
        return Err("无法读取工作空间更改。".into());
    }
    let mut records = output
        .stdout
        .split(|byte| *byte == 0)
        .filter(|record| !record.is_empty());
    let mut files = Vec::new();
    let mut truncated = false;
    while let Some(record) = records.next() {
        if record.len() < 4 {
            return Err("Git 返回了无效的文件状态。".into());
        }
        let mut status = std::str::from_utf8(&record[..2])
            .map_err(|_| "Git 状态无效。")?
            .to_string();
        let repository_path = std::str::from_utf8(&record[3..])
            .map_err(|_| "Git 文件名不是 UTF-8。")?
            .to_string();
        let had_previous = status.contains('R') || status.contains('C');
        let mut previous_path = if had_previous {
            let previous = std::str::from_utf8(records.next().ok_or("Git 重命名记录不完整。")?)
                .map_err(|_| "Git 文件名不是 UTF-8。")?;
            scoped_git_path(&prefix, previous)
        } else {
            None
        };
        let path = match (
            scoped_git_path(&prefix, &repository_path),
            previous_path.as_ref(),
        ) {
            (Some(path), _) => {
                if had_previous && previous_path.is_none() && !prefix.is_empty() {
                    status = "A".into();
                }
                path
            }
            (None, Some(previous)) => {
                let path = previous.clone();
                previous_path = None;
                status = "D".into();
                path
            }
            (None, None) => continue,
        };
        if files.len() >= ENTRY_LIMIT {
            truncated = true;
            break;
        }
        // Skip symlinks and malformed status paths rather than handing the UI an escape target.
        if resolve(root, &path, false, true).is_err() {
            continue;
        }
        files.push(FileChange {
            path,
            status: status.trim().to_string(),
            previous_path,
            added: None,
            removed: None,
        });
    }
    if let Ok(stats) = git(
        root,
        &[
            "diff",
            "--numstat",
            "-z",
            "--no-ext-diff",
            "--no-textconv",
            "--relative",
            "HEAD",
            "--",
            ".",
        ],
    ) {
        if stats.status.success() {
            let mut records = stats
                .stdout
                .split(|byte| *byte == 0)
                .filter(|record| !record.is_empty());
            while let Some(record) = records.next() {
                let mut fields = record.splitn(3, |byte| *byte == b'\t');
                let added = fields
                    .next()
                    .and_then(|value| std::str::from_utf8(value).ok())
                    .and_then(|value| value.parse::<u64>().ok());
                let removed = fields
                    .next()
                    .and_then(|value| std::str::from_utf8(value).ok())
                    .and_then(|value| value.parse::<u64>().ok());
                let path = fields.next().unwrap_or_default();
                let path = if path.is_empty() {
                    let _previous = records.next();
                    records.next().unwrap_or_default()
                } else {
                    path
                };
                if let Ok(path) = std::str::from_utf8(path) {
                    if let Some(file) = files.iter_mut().find(|file| file.path == path) {
                        file.added = added;
                        file.removed = removed;
                    }
                }
            }
        }
    }
    Ok(ChangeSet {
        files,
        repository: true,
        truncated,
    })
}

fn new_file_diff(root: &Path, path: &str) -> Result<FileDiff, String> {
    let document = match read(root, path) {
        Ok(document) => document,
        Err(error) if error == "此文件不是 UTF-8 文本，暂不支持预览。" => {
            return Ok(FileDiff {
                text: String::new(),
                binary: true,
                truncated: false,
                scope: "new".into(),
            });
        }
        Err(error) => return Err(error),
    };
    let Some(content) = document.text else {
        return Ok(FileDiff {
            text: String::new(),
            binary: true,
            truncated: false,
            scope: "new".into(),
        });
    };
    let lines = content.split_terminator('\n').count();
    let mut text = format!(
        "diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n"
    );
    if lines > 0 {
        text.push_str(&format!("@@ -0,0 +1,{lines} @@\n"));
        for line in content.split_terminator('\n') {
            text.push('+');
            text.push_str(line);
            text.push('\n');
        }
        if !content.ends_with('\n') {
            text.push_str("\\ No newline at end of file\n");
        }
    }
    Ok(FileDiff {
        text,
        binary: false,
        truncated: false,
        scope: "new".into(),
    })
}

pub fn diff(root: &Path, path: &str) -> Result<FileDiff, String> {
    resolve(root, path, false, true)?;
    if repository_prefix(root)?.is_none() {
        return Err("当前工作空间不在 Git 仓库中，无法显示更改。".into());
    }
    let head = git(root, &["rev-parse", "--verify", "HEAD"])?;
    if !head.status.success() {
        return new_file_diff(root, path);
    }
    let output = git(
        root,
        &[
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--no-color",
            "--relative",
            "HEAD",
            "--",
            path,
        ],
    )?;
    let output = if output.status.success() {
        output
    } else {
        git(
            root,
            &[
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--no-color",
                "--relative",
                "--",
                path,
            ],
        )?
    };
    if !output.status.success() {
        return Err("无法读取此文件的更改。".into());
    }
    let mut text = String::from_utf8(output.stdout).map_err(|_| "此更改不是 UTF-8 文本。")?;
    let mut scope = "worktree";
    if text.is_empty() {
        let tracked = git(root, &["ls-files", "--error-unmatch", "--", path])?;
        if !tracked.status.success() {
            return new_file_diff(root, path);
        }
        let staged = git(
            root,
            &[
                "diff",
                "--cached",
                "--no-ext-diff",
                "--no-textconv",
                "--no-color",
                "--relative",
                "HEAD",
                "--",
                path,
            ],
        )?;
        if !staged.status.success() {
            return Err("无法读取暂存区更改。".into());
        }
        if !staged.stdout.is_empty() {
            text = String::from_utf8(staged.stdout).map_err(|_| "此更改不是 UTF-8 文本。")?;
            scope = "staged";
        }
    }
    let binary = text.contains("Binary files ") || text.contains("GIT binary patch");
    Ok(FileDiff {
        text,
        binary,
        truncated: false,
        scope: scope.into(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> PathBuf {
        let mut random = [0_u8; 8];
        getrandom::fill(&mut random).unwrap();
        let root = std::env::temp_dir().join(format!(
            "forge-files-{}-{:x}",
            std::process::id(),
            u64::from_ne_bytes(random)
        ));
        fs::create_dir_all(&root).unwrap();
        root
    }
    #[test]
    fn confines_paths_and_handles_text_binary_and_limits() {
        let root = fixture();
        fs::create_dir(root.join("src")).unwrap();
        fs::write(root.join("src/readme.md"), "# 测试\n").unwrap();
        fs::write(root.join("binary.dat"), [0, 1, 2]).unwrap();
        assert!(list(&root, "").unwrap().first().unwrap().directory);
        assert_eq!(
            read(&root, "src/readme.md").unwrap().text.as_deref(),
            Some("# 测试\n")
        );
        assert_eq!(read(&root, "binary.dat").unwrap().kind, "unsupported");
        for bad in [
            "../outside",
            "src/../readme.md",
            "C:/Windows/win.ini",
            "/outside",
            "src//readme.md",
            "src/readme.md:stream",
            "src/./readme.md",
        ] {
            assert!(read(&root, bad).is_err(), "{bad}");
        }
        let large = File::create(root.join("large.txt")).unwrap();
        large.set_len(TEXT_LIMIT + 1).unwrap();
        assert!(read(&root, "large.txt").is_err());
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn reads_git_changes_without_mutating_index_and_handles_rename() {
        let root = fixture();
        assert!(git(&root, &["init"]).unwrap().status.success());
        fs::write(root.join("tracked name.txt"), "first\n").unwrap();
        assert!(git(&root, &["add", "--", "tracked name.txt"])
            .unwrap()
            .status
            .success());
        assert!(git(
            &root,
            &[
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "-m",
                "fixture"
            ]
        )
        .unwrap()
        .status
        .success());
        fs::rename(root.join("tracked name.txt"), root.join("renamed file.txt")).unwrap();
        assert!(git(&root, &["add", "-A"]).unwrap().status.success());
        fs::write(root.join("untracked.txt"), "new\n").unwrap();
        fs::create_dir(root.join("new directory")).unwrap();
        fs::write(root.join("new directory/nested.txt"), "nested\n").unwrap();
        let before = fs::read(root.join(".git/index")).unwrap();
        let set = changes(&root).unwrap();
        assert!(set.repository);
        assert!(set.files.iter().any(|file| file.path == "renamed file.txt"
            && file.previous_path.as_deref() == Some("tracked name.txt")));
        assert!(set
            .files
            .iter()
            .any(|file| file.path == "new directory/nested.txt"));
        assert!(diff(&root, "renamed file.txt")
            .unwrap()
            .text
            .contains("renamed file.txt"));
        assert!(diff(&root, "untracked.txt").unwrap().text.contains("+new"));
        assert_eq!(before, fs::read(root.join(".git/index")).unwrap());
        assert!(diff(&root, "../outside").is_err());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn refuses_links_and_preserves_image_and_pdf_bytes() {
        let root = fixture();
        fs::write(root.join("sample.pdf"), b"%PDF-1.7\nfixture").unwrap();
        fs::write(root.join("sample.png"), [137, 80, 78, 71, 13, 10, 26, 10]).unwrap();
        assert_eq!(
            read(&root, "sample.pdf").unwrap().mime.as_deref(),
            Some("application/pdf")
        );
        assert_eq!(
            read(&root, "sample.png").unwrap().bytes.unwrap(),
            vec![137, 80, 78, 71, 13, 10, 26, 10]
        );
        #[cfg(windows)]
        {
            // Windows developer mode/privilege may disallow creating the fixture link.
            if std::os::windows::fs::symlink_file(root.join("sample.pdf"), root.join("linked.pdf"))
                .is_ok()
            {
                assert!(read(&root, "linked.pdf").is_err());
                assert!(!list(&root, "")
                    .unwrap()
                    .iter()
                    .any(|file| file.name == "linked.pdf"));
            }
        }
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn diffs_initial_staged_untracked_and_binary_files_from_actual_content() {
        let root = fixture();
        assert!(git(&root, &["init"]).unwrap().status.success());
        fs::write(root.join("staged.txt"), "staged\n").unwrap();
        assert!(git(&root, &["add", "--", "staged.txt"])
            .unwrap()
            .status
            .success());
        fs::write(root.join("staged.txt"), "current\r\nlast").unwrap();
        fs::write(root.join("untracked.txt"), "new\n").unwrap();
        fs::write(root.join("binary.dat"), [0xff, 0xfe, 0xfd]).unwrap();
        fs::write(root.join("empty.txt"), "").unwrap();
        let before = fs::read(root.join(".git/index")).unwrap();
        let staged = diff(&root, "staged.txt").unwrap();
        assert!(staged.text.contains("+current\r\n+last\n"));
        assert!(staged.text.contains("\\ No newline at end of file\n"));
        assert!(!staged.text.contains("+staged"));
        assert!(diff(&root, "untracked.txt")
            .unwrap()
            .text
            .contains("+new\n"));
        assert!(diff(&root, "binary.dat").unwrap().binary);
        assert!(diff(&root, "empty.txt")
            .unwrap()
            .text
            .contains("new file mode"));
        assert_eq!(before, fs::read(root.join(".git/index")).unwrap());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn scopes_nested_workspace_and_uses_literal_file_paths() {
        let root = fixture();
        let workspace = root.join("nested space");
        fs::create_dir_all(&workspace).unwrap();
        assert!(git(&root, &["init"]).unwrap().status.success());
        for (path, text) in [
            ("outside.txt", "outside\n"),
            ("nested space/item[1].txt", "literal\n"),
            ("nested space/item1.txt", "pattern\n"),
            ("nested space/old.txt", "rename\n"),
            ("nested space/deleted.txt", "deleted\n"),
            ("nested space/moved-out.txt", "moved out\n"),
            ("moved-in.txt", "moved in\n"),
        ] {
            fs::write(root.join(path), text).unwrap();
        }
        assert!(git(&root, &["add", "-A"]).unwrap().status.success());
        assert!(git(
            &root,
            &[
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "-m",
                "fixture"
            ]
        )
        .unwrap()
        .status
        .success());
        fs::write(root.join("outside.txt"), "outside changed\n").unwrap();
        fs::write(workspace.join("item[1].txt"), "literal changed\n").unwrap();
        fs::write(workspace.join("item1.txt"), "pattern changed\n").unwrap();
        fs::rename(workspace.join("old.txt"), workspace.join("new.txt")).unwrap();
        fs::remove_file(workspace.join("deleted.txt")).unwrap();
        fs::rename(workspace.join("moved-out.txt"), root.join("moved-out.txt")).unwrap();
        fs::rename(root.join("moved-in.txt"), workspace.join("moved-in.txt")).unwrap();
        assert!(git(
            &root,
            &[
                "add",
                "--",
                "nested space/moved-out.txt",
                "moved-out.txt",
                "moved-in.txt",
                "nested space/moved-in.txt"
            ]
        )
        .unwrap()
        .status
        .success());
        assert!(git(&workspace, &["add", "--", "old.txt", "new.txt"])
            .unwrap()
            .status
            .success());
        let before = fs::read(root.join(".git/index")).unwrap();
        let set = changes(&workspace).unwrap();
        assert!(set.repository);
        assert_eq!(set.files.len(), 6);
        assert!(set
            .files
            .iter()
            .any(|file| file.path == "deleted.txt" && file.status == "D"));
        assert!(set.files.iter().any(|file| file.path == "moved-out.txt"
            && file.status == "D"
            && file.previous_path.is_none()));
        assert!(set.files.iter().any(|file| file.path == "moved-in.txt"
            && file.status == "A"
            && file.previous_path.is_none()));
        assert!(!set
            .files
            .iter()
            .any(|file| file.path.contains("outside") || file.path.starts_with("nested space/")));
        assert!(
            set.files
                .iter()
                .any(|file| file.path == "new.txt"
                    && file.previous_path.as_deref() == Some("old.txt"))
        );
        assert!(set.files.iter().any(|file| file.path == "item[1].txt"
            && file.added == Some(1)
            && file.removed == Some(1)));
        let patch = diff(&workspace, "item[1].txt").unwrap();
        assert!(patch.text.contains("+literal changed"));
        assert!(!patch.text.contains("pattern changed"));
        assert!(!patch.text.contains("nested space/"));
        assert!(diff(&workspace, "deleted.txt")
            .unwrap()
            .text
            .contains("-deleted"));
        assert!(diff(&workspace, "moved-out.txt")
            .unwrap()
            .text
            .contains("-moved out"));
        assert_eq!(before, fs::read(root.join(".git/index")).unwrap());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn preserves_staged_diff_when_worktree_matches_head_without_mutating_index() {
        let root = fixture();
        assert!(git(&root, &["init"]).unwrap().status.success());
        fs::write(root.join("tracked.txt"), "baseline\n").unwrap();
        assert!(git(&root, &["add", "--", "tracked.txt"])
            .unwrap()
            .status
            .success());
        assert!(git(
            &root,
            &[
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "-m",
                "fixture"
            ]
        )
        .unwrap()
        .status
        .success());
        fs::write(root.join("tracked.txt"), "staged\n").unwrap();
        assert!(git(&root, &["add", "--", "tracked.txt"])
            .unwrap()
            .status
            .success());
        fs::write(root.join("tracked.txt"), "baseline\n").unwrap();
        let before = fs::read(root.join(".git/index")).unwrap();
        assert!(changes(&root)
            .unwrap()
            .files
            .iter()
            .any(|file| file.path == "tracked.txt" && file.status == "MM"));
        let staged = diff(&root, "tracked.txt").unwrap();
        assert_eq!(staged.scope, "staged");
        assert!(staged.text.contains("+staged\n"));
        assert!(staged.text.contains("-baseline\n"));
        assert_eq!(before, fs::read(root.join(".git/index")).unwrap());
        fs::write(root.join("tracked.txt"), "working tree\n").unwrap();
        let working = diff(&root, "tracked.txt").unwrap();
        assert_eq!(working.scope, "worktree");
        assert!(working.text.contains("+working tree\n"));
        assert_eq!(before, fs::read(root.join(".git/index")).unwrap());
        fs::remove_dir_all(root).unwrap();
    }
}
