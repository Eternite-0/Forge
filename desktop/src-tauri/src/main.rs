#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use forge_desktop::{
    core_host::{Connection, CoreHost, Status},
    credential_vault,
};
use serde_json::{json, Value};
use tauri::{Manager, State, WebviewWindow};

fn main_window(window: &WebviewWindow) -> Result<(), String> {
    if window.label() == "main" {
        Ok(())
    } else {
        Err("UNAUTHORIZED_WINDOW".into())
    }
}
#[tauri::command]
fn connection_get(window: WebviewWindow, host: State<'_, CoreHost>) -> Result<Connection, String> {
    main_window(&window)?;
    Ok(host.connection())
}
#[tauri::command]
fn runtime_status(window: WebviewWindow, host: State<'_, CoreHost>) -> Result<Status, String> {
    main_window(&window)?;
    Ok(host.status())
}
#[tauri::command]
fn runtime_retry(window: WebviewWindow, host: State<'_, CoreHost>) -> Result<Value, String> {
    main_window(&window)?;
    host.restart();
    Ok(json!({"requested":true}))
}
#[tauri::command]
fn credential_set(
    window: WebviewWindow,
    host: State<'_, CoreHost>,
    provider_id: String,
    credential_ref: String,
    mut secret: String,
) -> Result<Value, String> {
    let result = main_window(&window).and_then(|_| {
        credential_vault::set(&host.namespace, &provider_id, &credential_ref, &secret)
            .map_err(|_| "CREDENTIAL_WRITE_FAILED".to_string())
    });
    unsafe {
        for byte in secret.as_bytes_mut() {
            std::ptr::write_volatile(byte, 0);
        }
    }
    result?;
    host.restart();
    Ok(json!({"stored":true}))
}
#[tauri::command]
fn credential_delete(
    window: WebviewWindow,
    host: State<'_, CoreHost>,
    provider_id: String,
    credential_ref: String,
) -> Result<Value, String> {
    main_window(&window)?;
    let deleted = credential_vault::delete(&host.namespace, &provider_id, &credential_ref)
        .map_err(|_| "CREDENTIAL_DELETE_FAILED".to_string())?;
    if deleted {
        host.restart();
    }
    Ok(json!({"deleted":deleted}))
}
#[tauri::command]
fn credential_exists(
    window: WebviewWindow,
    host: State<'_, CoreHost>,
    provider_id: String,
    credential_ref: String,
) -> Result<Value, String> {
    main_window(&window)?;
    let exists = credential_vault::exists(&host.namespace, &provider_id, &credential_ref)
        .map_err(|_| "CREDENTIAL_LOOKUP_FAILED".to_string())?;
    Ok(json!({"exists":exists}))
}
#[tauri::command]
fn workspace_backup(
    window: WebviewWindow,
    host: State<'_, CoreHost>,
    path: String,
) -> Result<Value, String> {
    main_window(&window)?;
    host.maintenance("backup", path.into())
        .map_err(|_| "MAINTENANCE_FAILED".into())
}
#[tauri::command]
fn workspace_restore(
    window: WebviewWindow,
    host: State<'_, CoreHost>,
    path: String,
) -> Result<Value, String> {
    main_window(&window)?;
    host.maintenance("restore", path.into())
        .map_err(|_| "MAINTENANCE_FAILED".into())
}
#[tauri::command]
fn workspace_operation_status(
    window: WebviewWindow,
    host: State<'_, CoreHost>,
    operation_id: Option<String>,
) -> Result<Value, String> {
    main_window(&window)?;
    host.operation_status(operation_id.as_deref())
        .map_err(|_| "OPERATION_NOT_FOUND".into())
}
#[tauri::command]
fn workspace_operation_acknowledge(
    window: WebviewWindow,
    host: State<'_, CoreHost>,
    operation_id: String,
    reason: String,
) -> Result<Value, String> {
    main_window(&window)?;
    host.acknowledge_unknown(&operation_id, &reason)
        .map_err(|_| "ACKNOWLEDGEMENT_FAILED".into())
}
#[tauri::command]
fn open_external(window: WebviewWindow, url: String) -> Result<Value, String> {
    main_window(&window)?;
    let url = forge_desktop::normalized_external_url(&url).ok_or("INVALID_URL")?;
    let wide: Vec<u16> = url.encode_utf16().chain(Some(0)).collect();
    let operation: Vec<u16> = "open".encode_utf16().chain(Some(0)).collect();
    let result = unsafe {
        windows_sys::Win32::UI::Shell::ShellExecuteW(
            std::ptr::null_mut(),
            operation.as_ptr(),
            wide.as_ptr(),
            std::ptr::null(),
            std::ptr::null(),
            windows_sys::Win32::UI::WindowsAndMessaging::SW_SHOWNORMAL,
        )
    };
    if result as isize <= 32 {
        return Err("OPEN_FAILED".into());
    }
    Ok(json!({"opened":true}))
}
fn main() {
    tauri::Builder::default()
        .plugin(
            tauri::plugin::Builder::<tauri::Wry, ()>::new("navigation-guard")
                .on_navigation(|_, url| {
                    (url.scheme() == "http"
                        && url.host_str() == Some("tauri.localhost")
                        && url.port().is_none())
                        || (url.scheme() == "tauri" && url.host_str() == Some("localhost"))
                        || (cfg!(debug_assertions)
                            && url.scheme() == "http"
                            && url.host_str() == Some("localhost")
                            && url.port() == Some(5173))
                })
                .build(),
        )
        .setup(|app| {
            let runtime = if cfg!(debug_assertions) {
                std::env::var_os("FORGE_DESKTOP_RUNTIME")
                    .map(std::path::PathBuf::from)
                    .unwrap_or(app.path().resource_dir()?.join("resources/runtime"))
            } else {
                app.path().resource_dir()?.join("resources/runtime")
            };
            if !runtime.is_absolute() {
                return Err("Runtime path must be absolute".into());
            }
            app.manage(CoreHost::start(runtime)?);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            connection_get,
            runtime_status,
            runtime_retry,
            credential_set,
            credential_delete,
            credential_exists,
            workspace_backup,
            workspace_restore,
            workspace_operation_status,
            workspace_operation_acknowledge,
            open_external
        ])
        .on_window_event(|window, event| {
            if window.label() == "main" {
                if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                    let host = window.state::<CoreHost>();
                    if host.status().state == "maintenance" {
                        api.prevent_close();
                    } else {
                        host.stop();
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("FORGE desktop application failed");
}
