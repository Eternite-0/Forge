pub mod core_host;
pub mod credential_vault;
pub mod protocol;
pub mod workspace_files;
pub fn normalized_external_url(value: &str) -> Option<String> {
    if value.len() > 2048
        || value
            .chars()
            .any(|c| c.is_control() || c.is_whitespace() || c == '"' || c == '\'')
        || !(value.starts_with("https://") || value.starts_with("http://"))
    {
        return None;
    }
    match tauri::Url::parse(value) {
        Ok(url)
            if matches!(url.scheme(), "http" | "https")
                && url.host_str().is_some()
                && url.username().is_empty()
                && url.password().is_none() =>
        {
            Some(url.to_string())
        }
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::protocol::{read_frame, write_frame, Bootstrap};
    use std::io::Cursor;

    #[test]
    fn framed_messages_roundtrip_and_reject_oversized() {
        let input = serde_json::json!({"version": 1, "type": "ready", "port": 1234});
        let mut bytes = Vec::new();
        write_frame(&mut bytes, &input).unwrap();
        assert_eq!(read_frame(&mut Cursor::new(bytes)).unwrap(), input);
        assert!(read_frame(&mut Cursor::new(65537_u32.to_be_bytes())).is_err());
    }

    #[test]
    fn bootstrap_rejects_unscoped_credentials() {
        let value = serde_json::json!({"version": 1, "type": "bootstrap", "secret": "ab".repeat(32), "workspace_root": "C:\\temp", "credentials": {"PATH": "bad"}});
        assert!(Bootstrap::validate(&value).is_err());
    }

    #[test]
    fn external_url_restricts_scheme_and_userinfo() {
        assert_eq!(
            super::normalized_external_url("https://EXAMPLE.invalid:443/a/../path?q=1"),
            Some("https://example.invalid/path?q=1".into())
        );
        for value in [
            "javascript:alert(1)",
            "file:///tmp/x",
            "https://user:pass@example.invalid/",
            "https://example.invalid/\nfoo",
            "http:\\\\evil.invalid",
            "https://example.invalid/a path",
            "https://example.invalid/a\"b",
        ] {
            assert!(super::normalized_external_url(value).is_none());
        }
    }

    #[test]
    fn temporary_windows_credential_roundtrip() {
        let namespace = "f".repeat(64);
        let reference = "FORGE_PROVIDER_KEY_NATIVE_TEST";
        let provider = format!("native-test-{}", std::process::id());
        assert!(
            super::credential_vault::set(&namespace, "bad.provider", reference, "dummy-only")
                .is_err()
        );
        assert!(
            super::credential_vault::set(&namespace, &provider, reference, "nonascii-测试")
                .is_err()
        );
        assert!(!super::credential_vault::exists(&namespace, &provider, reference).unwrap());
        super::credential_vault::set(&namespace, &provider, reference, "dummy-only").unwrap();
        let inactive = format!("inactive-{}", std::process::id());
        super::credential_vault::set(&namespace, &inactive, reference, "old-dummy").unwrap();
        assert!(super::credential_vault::exists(&namespace, &provider, reference).unwrap());
        assert_eq!(
            super::credential_vault::read_one(&namespace, &provider, reference)
                .unwrap()
                .as_deref(),
            Some("dummy-only")
        );
        assert!(super::credential_vault::delete(&namespace, &inactive, reference).unwrap());
        assert!(super::credential_vault::delete(&namespace, &provider, reference).unwrap());
        assert!(!super::credential_vault::exists(&namespace, &provider, reference).unwrap());
    }
}
