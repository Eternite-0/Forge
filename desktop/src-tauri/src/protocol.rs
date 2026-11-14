use serde_json::Value;
use std::io::{self, Read, Write};

const MAX_FRAME: usize = 65_536;

pub fn write_frame<W: Write>(writer: &mut W, value: &Value) -> io::Result<()> {
    let mut bytes = serde_json::to_vec(value).map_err(io::Error::other)?;
    if bytes.len() > MAX_FRAME {
        for byte in &mut bytes {
            unsafe {
                std::ptr::write_volatile(byte, 0);
            }
        }
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "frame too large",
        ));
    }
    let result = (|| {
        writer.write_all(&(bytes.len() as u32).to_be_bytes())?;
        writer.write_all(&bytes)?;
        writer.flush()
    })();
    for byte in &mut bytes {
        unsafe {
            std::ptr::write_volatile(byte, 0);
        }
    }
    result
}

pub fn clear_values(value: &mut Value) {
    match value {
        Value::String(text) => unsafe {
            for byte in text.as_bytes_mut() {
                std::ptr::write_volatile(byte, 0);
            }
        },
        Value::Array(items) => {
            for item in items {
                clear_values(item);
            }
        }
        Value::Object(items) => {
            for item in items.values_mut() {
                clear_values(item);
            }
        }
        _ => (),
    }
}

pub fn read_frame<R: Read>(reader: &mut R) -> io::Result<Value> {
    let mut header = [0; 4];
    reader.read_exact(&mut header)?;
    let length = u32::from_be_bytes(header) as usize;
    if length > MAX_FRAME || length == 0 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "invalid frame length",
        ));
    }
    let mut bytes = vec![0; length];
    reader.read_exact(&mut bytes)?;
    serde_json::from_slice(&bytes).map_err(io::Error::other)
}

pub struct Bootstrap;
impl Bootstrap {
    pub fn validate(value: &Value) -> io::Result<()> {
        let credentials = value
            .get("credentials")
            .and_then(Value::as_object)
            .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "invalid credentials"))?;
        for (key, value) in credentials {
            if !valid_credential_ref(key)
                || !value
                    .as_str()
                    .is_some_and(|s| !s.is_empty() && s.len() <= 8192)
            {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "invalid credential",
                ));
            }
        }
        Ok(())
    }
}

pub fn valid_credential_ref(value: &str) -> bool {
    value
        .strip_prefix("FORGE_PROVIDER_KEY_")
        .is_some_and(|tail| {
            !tail.is_empty()
                && tail.len() <= 128
                && tail
                    .bytes()
                    .all(|c| c.is_ascii_uppercase() || c.is_ascii_digit() || c == b'_')
        })
}
