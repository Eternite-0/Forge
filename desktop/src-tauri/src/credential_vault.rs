use crate::protocol::valid_credential_ref;
use std::{io, ptr};
use windows_sys::Win32::{
    Foundation::{GetLastError, ERROR_NOT_FOUND},
    Security::Credentials::{
        CredDeleteW, CredFree, CredReadW, CredWriteW, CREDENTIALW, CRED_PERSIST_LOCAL_MACHINE,
        CRED_TYPE_GENERIC,
    },
};

fn wide(value: &str) -> Vec<u16> {
    value.encode_utf16().chain(Some(0)).collect()
}
fn valid_provider(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 100
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}
fn target(namespace: &str, provider: &str, reference: &str) -> io::Result<String> {
    if namespace.len() != 64
        || !namespace.bytes().all(|b| b.is_ascii_hexdigit())
        || !valid_provider(provider)
        || !valid_credential_ref(reference)
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "invalid credential identity",
        ));
    }
    Ok(format!("FORGE/{namespace}/{provider}/{reference}"))
}
pub fn set(namespace: &str, provider: &str, reference: &str, secret: &str) -> io::Result<()> {
    if secret.is_empty()
        || secret.len() > 2560
        || !secret.bytes().all(|b| (0x20..=0x7e).contains(&b))
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "invalid credential",
        ));
    }
    let mut name = wide(&target(namespace, provider, reference)?);
    let mut bytes = secret.as_bytes().to_vec();
    let entry = CREDENTIALW {
        Type: CRED_TYPE_GENERIC,
        TargetName: name.as_mut_ptr(),
        CredentialBlobSize: bytes.len() as u32,
        CredentialBlob: bytes.as_mut_ptr(),
        Persist: CRED_PERSIST_LOCAL_MACHINE,
        ..Default::default()
    };
    let result = unsafe { CredWriteW(&entry, 0) };
    bytes.fill(0);
    if result == 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}
pub fn delete(namespace: &str, provider: &str, reference: &str) -> io::Result<bool> {
    let name = wide(&target(namespace, provider, reference)?);
    if unsafe { CredDeleteW(name.as_ptr(), CRED_TYPE_GENERIC, 0) } != 0 {
        return Ok(true);
    }
    if unsafe { GetLastError() } == ERROR_NOT_FOUND {
        return Ok(false);
    }
    Err(io::Error::last_os_error())
}
pub fn exists(namespace: &str, provider: &str, reference: &str) -> io::Result<bool> {
    let name = wide(&target(namespace, provider, reference)?);
    let mut credential = ptr::null_mut();
    if unsafe { CredReadW(name.as_ptr(), CRED_TYPE_GENERIC, 0, &mut credential) } == 0 {
        if unsafe { GetLastError() } == ERROR_NOT_FOUND {
            return Ok(false);
        }
        return Err(io::Error::last_os_error());
    }
    unsafe {
        ptr::write_bytes(
            (*credential).CredentialBlob,
            0,
            (*credential).CredentialBlobSize as usize,
        );
        CredFree(credential.cast());
    }
    Ok(true)
}
pub(crate) fn read_one(
    namespace: &str,
    provider: &str,
    reference: &str,
) -> io::Result<Option<String>> {
    let name = wide(&target(namespace, provider, reference)?);
    let mut credential = ptr::null_mut();
    if unsafe { CredReadW(name.as_ptr(), CRED_TYPE_GENERIC, 0, &mut credential) } == 0 {
        if unsafe { GetLastError() } == ERROR_NOT_FOUND {
            return Ok(None);
        }
        return Err(io::Error::last_os_error());
    }
    let result = unsafe {
        String::from_utf8(
            std::slice::from_raw_parts(
                (*credential).CredentialBlob,
                (*credential).CredentialBlobSize as usize,
            )
            .to_vec(),
        )
    };
    unsafe {
        ptr::write_bytes(
            (*credential).CredentialBlob,
            0,
            (*credential).CredentialBlobSize as usize,
        );
        CredFree(credential.cast());
    }
    result
        .map(Some)
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "invalid credential encoding"))
}
