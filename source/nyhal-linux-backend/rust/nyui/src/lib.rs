//! Nyrqis NUI (.nstudio) document parse/validate — ADR-0025.
//!
//! The UI runtime's import gate: given a `.nstudio` document (the JSON
//! intermediate representation NyForge produces — NFS-001), validate it
//! against the NUI contract tables before the shell trusts it. This
//! crate is the shipped form of the parse/validate hot path; the
//! pure-Python module `ui/nstudio.py` is the reference floor, and the
//! conformance gate (`TestNstudioCodecConformance`) forces the floor's
//! suite through this FFI unchanged — the ADR-0020 migration contract.
//!
//! The contract tables are embedded from the **Nyrqis API Registry**
//! (`ui/contracts/nui-api-v1.json`, `include_str!`-ed at compile time) —
//! the single machine-readable source of truth for the NUI component
//! vocabulary (NFS-006, ADR-0025). The pure-Python reference floor
//! (`ui/nstudio.py`) reads the same file, and NyForge regenerates its C#
//! tables from it. A registry change that isn't compiled into this crate
//! is a build failure, not a silent drift. Schema versioning per
//! NFS-001 §9: a document must declare `version == "0.4.0"` (this
//! crate's `SUPPORTED_SCHEMA_VERSION`), else validation fails loudly
//! instead of silently misinterpreting the file.
//!
//! **FFI surface (ABI 1.0.0).** Caller-supplied input only — the JSON
//! text is read in place, nothing is allocated on the Rust side, and
//! there is no `free` contract:
//!
//! - `nyrqis_nyui_version() -> u32` — ABI version (`0x0001_0000`).
//! - `nyrqis_nyui_validate(json_ptr, json_len) -> i32` — `0` = valid;
//!   negative = failure, one of the status codes below.
//! - `nyrqis_nyui_last_error(buf, cap) -> i32` — copies the last error
//!   message into a caller buffer (best-effort, for diagnostics).
//!
//! Status codes (all negative, outside the errno range 1..=4095, so the
//! loader's mapping can never collide with a real syscall error):
//!
//! | code    | meaning                            |
//! |---------|------------------------------------|
//! | `0`     | valid document                     |
//! | `-1`    | input is not valid UTF-8           |
//! | `-2`    | malformed JSON                     |
//! | `-3`    | unsupported schema version         |
//! | `-4`    | validation failed (see last error) |
//! | `-4096` | internal error                     |

use serde::Deserialize;
use serde_json::Value;
use std::cell::RefCell;
use std::ffi::c_char;
use std::ptr;
use std::sync::OnceLock;

// ---------------------------------------------------------------------------
// Contract tables — from the Nyrqis API Registry (NFS-006 / ADR-0025)
// ---------------------------------------------------------------------------

const SUPPORTED_SCHEMA_VERSION: &str = "0.4.0";

const ABI_VERSION: u32 = 0x0001_0000;

// Status codes (negative i32, outside the errno range 1..=4095).
const ERR_INVALID_UTF8: i32 = -1;
const ERR_MALFORMED_JSON: i32 = -2;
const ERR_VERSION: i32 = -3;
const ERR_VALIDATION: i32 = -4;
const ERR_INTERNAL: i32 = -4096;

/// The Nyrqis API Registry — one component entry.
#[derive(Debug, Deserialize)]
struct ComponentContract {
    #[serde(rename = "type")]
    type_name: String,
    #[allow(dead_code)]
    category: String,
    properties: Vec<String>,
    events: Vec<String>,
    actions: Vec<String>,
}

/// The Nyrqis API Registry — one system action entry.
#[derive(Debug, Deserialize)]
struct SystemAction {
    name: String,
    arguments: Vec<String>,
}

/// The Nyrqis API Registry, embedded at compile time.
///
/// The registry file (``ui/contracts/nui-api-v1.json``, one directory up
/// from the crate's parent) is the single machine-readable source of
/// truth for the NUI component vocabulary; both this crate and the
/// pure-Python reference floor derive their tables from it. Parsing once
/// on first use: a registry that fails to parse is a hard internal
/// error, never a silently empty table.
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct Registry {
    #[allow(dead_code)]
    registry_version: String,
    #[allow(dead_code)]
    nui_schema_version: String,
    #[allow(dead_code)]
    purpose: String,
    components: Vec<ComponentContract>,
    system_actions: Vec<SystemAction>,
}

static REGISTRY: OnceLock<Registry> = OnceLock::new();

fn registry() -> &'static Registry {
    REGISTRY.get_or_init(|| {
        serde_json::from_str(include_str!("../../../ui/contracts/nui-api-v1.json"))
            .expect("embedded Nyrqis API Registry must parse; check ui/contracts/nui-api-v1.json")
    })
}

fn contract(type_name: &str) -> Option<&'static ComponentContract> {
    registry().components.iter().find(|c| c.type_name == type_name)
}

fn system_action(name: &str) -> Option<&'static SystemAction> {
    registry().system_actions.iter().find(|a| a.name == name)
}

// ---------------------------------------------------------------------------
// Last-error slot (single-threaded FFI contract, like the other crates)
// ---------------------------------------------------------------------------

thread_local! {
    static LAST_ERROR: RefCell<String> = const { RefCell::new(String::new()) };
}

fn set_last_error(msg: impl AsRef<str>) {
    LAST_ERROR.with(|slot| *slot.borrow_mut() = msg.as_ref().to_string());
}

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------



/// Collect every component id in the document, document order.
fn collect_ids<'v>(node: &'v Value, out: &mut Vec<String>) {
    if let Some(id) = node.get("id").and_then(Value::as_str) {
        out.push(id.to_string());
    }
    if let Some(children) = node.get("children").and_then(Value::as_array) {
        for child in children {
            collect_ids(child, out);
        }
    }
}

/// Find a component node by id anywhere in the tree.
fn find_component<'v>(node: &'v Value, id: &str) -> Option<&'v Value> {
    if node.get("id").and_then(Value::as_str) == Some(id) {
        return Some(node);
    }
    if let Some(children) = node.get("children").and_then(Value::as_array) {
        for child in children {
            if let Some(found) = find_component(child, id) {
                return Some(found);
            }
        }
    }
    None
}

/// Validate a parsed `.nstudio` document. Returns `Ok(())` or the first
/// validation error (mirroring `ui/nstudio.py`'s `_validate`).
fn validate_document(raw: &Value) -> Result<(), String> {
    let version = raw
        .get("version")
        .and_then(Value::as_str)
        .ok_or_else(|| "document must declare a string 'version'".to_string())?;
    if version != SUPPORTED_SCHEMA_VERSION {
        return Err(format!(
            "unsupported schema version '{version}'; supported: {SUPPORTED_SCHEMA_VERSION}"
        ));
    }

    let states = raw.get("states").and_then(Value::as_object);

    // Pass 1: collect ids.
    let mut component_ids: Vec<String> = Vec::new();
    if let Some(screens) = raw.get("screens").and_then(Value::as_array) {
        for screen in screens {
            if let Some(root) = screen.get("root") {
                collect_ids(root, &mut component_ids);
            }
        }
    }

    // Duplicate component ids.
    {
        let mut seen: Vec<&str> = Vec::new();
        for id in &component_ids {
            if seen.contains(&id.as_str()) {
                return Err(format!("duplicate component id '{id}'"));
            }
            seen.push(id);
        }
    }

    // Pass 2: behaviors (ids first so component event refs can check them).
    let mut behavior_ids: Vec<String> = Vec::new();
    if let Some(behaviors) = raw.get("behaviors").and_then(Value::as_array) {
        for behavior in behaviors {
            let id = behavior
                .get("id")
                .and_then(Value::as_str)
                .ok_or_else(|| "behavior entries must declare a string 'id'".to_string())?;
            if behavior_ids.iter().any(|b| b == id) {
                return Err(format!("duplicate behavior id '{id}'"));
            }
            behavior_ids.push(id.to_string());
        }
    }

    // Pass 3: components.
    if let Some(screens) = raw.get("screens").and_then(Value::as_array) {
        for screen in screens {
            if let Some(root) = screen.get("root") {
                validate_component(root, &behavior_ids)?;
            }
        }
    }

    // Pass 4: behaviors (full context).
    if let Some(behaviors) = raw.get("behaviors").and_then(Value::as_array) {
        for behavior in behaviors {
            validate_behavior(behavior, states, &component_ids, raw)?;
        }
    }

    // Pass 5: bindings.
    if let Some(bindings) = raw.get("bindings").and_then(Value::as_array) {
        for binding in bindings {
            validate_binding(binding, states, &component_ids, raw)?;
        }
    }

    Ok(())
}

fn validate_component(node: &Value, behavior_ids: &[String]) -> Result<(), String> {
    let id = node
        .get("id")
        .and_then(Value::as_str)
        .ok_or_else(|| "component nodes must declare a string 'id'".to_string())?;
    let type_name = node
        .get("type")
        .and_then(Value::as_str)
        .ok_or_else(|| format!("component '{id}' must declare a string 'type'"))?;

    let contract = contract(type_name)
        .ok_or_else(|| format!("component '{id}': unknown type '{type_name}'"))?;

    if let Some(props) = node.get("properties").and_then(Value::as_object) {
        for key in props.keys() {
            if !contract.properties.iter().any(|p| p == key) {
                return Err(format!(
                    "component '{id}': property '{key}' not in the '{type_name}' contract"
                ));
            }
        }
    }

    if let Some(events_map) = node.get("events").and_then(Value::as_object) {
        for (event, behavior) in events_map {
            if !contract.events.iter().any(|e| e == event) {
                return Err(format!(
                    "component '{id}': event '{event}' not in the '{type_name}' contract"
                ));
            }
            if let Some(target) = behavior.as_str() {
                if !behavior_ids.iter().any(|b| b == target) {
                    return Err(format!(
                        "component '{id}': event '{event}' references unknown behavior '{target}'"
                    ));
                }
            }
        }
    }

    if let Some(layout) = node.get("layout").and_then(Value::as_object) {
        for key in ["x", "y", "width", "height"] {
            let ok = matches!(
                layout.get(key),
                Some(Value::Number(n)) if n.is_i64() && n.as_i64().unwrap_or(-1) >= 0
            );
            if !ok {
                return Err(format!(
                    "component '{id}': layout '{key}' must be a non-negative integer"
                ));
            }
        }
    }

    if let Some(children) = node.get("children").and_then(Value::as_array) {
        for child in children {
            validate_component(child, behavior_ids)?;
        }
    }
    Ok(())
}

fn validate_behavior(
    behavior: &Value,
    states: Option<&serde_json::Map<String, Value>>,
    component_ids: &[String],
    raw: &Value,
) -> Result<(), String> {
    let id = behavior
        .get("id")
        .and_then(Value::as_str)
        .ok_or_else(|| "behavior entries must declare a string 'id'".to_string())?;

    if let Some(condition) = behavior.get("condition") {
        if !condition.is_null() {
            let state_key = condition
                .get("state")
                .and_then(Value::as_str)
                .ok_or_else(|| format!("behavior '{id}': condition must declare a 'state'"))?;
            if !states.map(|s| s.contains_key(state_key)).unwrap_or(false) {
                return Err(format!(
                    "behavior '{id}': condition references unknown state '{state_key}'"
                ));
            }
            let operator = condition
                .get("operator")
                .and_then(Value::as_str)
                .ok_or_else(|| format!("behavior '{id}': condition must declare an 'operator'"))?;
            if operator != "equals" && operator != "notEquals" {
                return Err(format!(
                    "behavior '{id}': condition operator must be 'equals' or 'notEquals'"
                ));
            }
        }
    }

    let action = behavior
        .get("action")
        .ok_or_else(|| format!("behavior '{id}' must declare an 'action'"))?;
    let name = action
        .get("name")
        .and_then(Value::as_str)
        .ok_or_else(|| format!("behavior '{id}': action must declare a 'name'"))?;
    let target = action
        .get("target")
        .and_then(Value::as_str)
        .ok_or_else(|| format!("behavior '{id}': action must declare a 'target'"))?;

    if target == "System" {
        match system_action(name) {
            Some(sys_action) => {
                if let Some(args) = action.get("arguments").and_then(Value::as_object) {
                    for arg in args.keys() {
                        if !sys_action.arguments.iter().any(|a| a == arg) {
                            return Err(format!(
                                "behavior '{id}': argument '{arg}' not in the '{name}' contract"
                            ));
                        }
                    }
                }
            }
            None => return Err(format!("behavior '{id}': unknown system action '{name}'")),
        }
    } else if let Some(component) = find_component_in_doc(raw, target) {
        let type_name = component
            .get("type")
            .and_then(Value::as_str)
            .ok_or_else(|| format!("behavior '{id}': target component '{target}' has no type"))?;
        let actions = contract(type_name).map(|c| c.actions.as_slice()).unwrap_or(&[]);
        if !actions.iter().any(|a| a == name) {
            return Err(format!(
                "behavior '{id}': action '{name}' not declared by component '{target}'"
            ));
        }
    } else {
        return Err(format!(
            "behavior '{id}': action target '{target}' is neither 'System' nor a component id"
        ));
    }
    let _ = component_ids;
    Ok(())
}

fn find_component_in_doc<'v>(raw: &'v Value, id: &str) -> Option<&'v Value> {
    if let Some(screens) = raw.get("screens").and_then(Value::as_array) {
        for screen in screens {
            if let Some(root) = screen.get("root") {
                if let Some(found) = find_component(root, id) {
                    return Some(found);
                }
            }
        }
    }
    None
}

fn validate_binding(
    binding: &Value,
    states: Option<&serde_json::Map<String, Value>>,
    component_ids: &[String],
    raw: &Value,
) -> Result<(), String> {
    let component = binding
        .get("component")
        .and_then(Value::as_str)
        .ok_or_else(|| "binding entries must declare a string 'component'".to_string())?;
    let state = binding
        .get("state")
        .and_then(Value::as_str)
        .ok_or_else(|| "binding entries must declare a string 'state'".to_string())?;
    let property = binding
        .get("property")
        .and_then(Value::as_str)
        .ok_or_else(|| "binding entries must declare a string 'property'".to_string())?;

    if !component_ids.iter().any(|cid| cid == component) {
        return Err(format!("binding: component '{component}' does not exist"));
    }
    if !states.map(|s| s.contains_key(state)).unwrap_or(false) {
        return Err(format!("binding: state '{state}' does not exist"));
    }
    if let Some(node) = find_component_in_doc(raw, component) {
        let type_name = node
            .get("type")
            .and_then(Value::as_str)
            .ok_or_else(|| format!("binding: component '{component}' has no type"))?;
        let properties = contract(type_name).map(|c| c.properties.as_slice()).unwrap_or(&[]);
        if !properties.iter().any(|p| p == property) {
            return Err(format!(
                "binding: property '{property}' not in the '{component}' contract"
            ));
        }
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// FFI
// ---------------------------------------------------------------------------

/// ABI version of this crate's FFI surface (1.0.0).
#[no_mangle]
pub extern "C" fn nyrqis_nyui_version() -> u32 {
    ABI_VERSION
}

/// Validate a `.nstudio` document given as UTF-8 bytes.
///
/// Returns `0` on success or a negative status code (see module docs).
/// On failure the reason is available via `nyrqis_nyui_last_error`.
///
/// # Safety
/// `json_ptr` must point to `json_len` readable bytes.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_nyui_validate(json_ptr: *const c_char, json_len: usize) -> i32 {
    if json_ptr.is_null() {
        set_last_error("null input pointer");
        return ERR_INTERNAL;
    }
    let bytes = unsafe { std::slice::from_raw_parts(json_ptr as *const u8, json_len) };
    let text = match std::str::from_utf8(bytes) {
        Ok(t) => t,
        Err(_) => {
            set_last_error("input is not valid UTF-8");
            return ERR_INVALID_UTF8;
        }
    };
    let raw: Value = match serde_json::from_str(text) {
        Ok(v) => v,
        Err(e) => {
            set_last_error(format!("malformed JSON: {e}"));
            return ERR_MALFORMED_JSON;
        }
    };
    match validate_document(&raw) {
        Ok(()) => 0,
        Err(msg) => {
            let is_version = msg.starts_with("unsupported schema version");
            set_last_error(&msg);
            if is_version {
                ERR_VERSION
            } else {
                ERR_VALIDATION
            }
        }
    }
}

/// Copy the last error message into a caller-supplied buffer. Returns the
/// number of bytes written (excluding the NUL terminator), or `-1` if the
/// buffer is too small (the message is truncated to fit, still NUL
/// terminated).
///
/// # Safety
/// `buf` must point to `cap` writable bytes; `cap` must be > 0.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_nyui_last_error(buf: *mut c_char, cap: usize) -> i32 {
    if buf.is_null() || cap == 0 {
        return -1;
    }
    let message = LAST_ERROR.with(|slot| slot.borrow().clone());
    let bytes = message.as_bytes();
    let n = bytes.len().min(cap - 1);
    unsafe {
        ptr::copy_nonoverlapping(bytes.as_ptr(), buf as *mut u8, n);
        *buf.add(n) = 0;
    }
    n as i32
}

// ---------------------------------------------------------------------------
// Unit tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    const VALID_SHELL: &str = r#"{
      "version": "0.4.0",
      "project": { "name": "Nyrqis Shell" },
      "states": { "doNotDisturb": false, "lastRefresh": "12:04" },
      "behaviors": [
        { "id": "behavior_refresh", "condition": null,
          "action": { "target": "System", "name": "Nyrqis.Notification.Show",
                      "arguments": { "title": "Workspace refreshed", "message": "$state:lastRefresh", "severity": "info" } } }
      ],
      "bindings": [ { "component": "toggle_dnd", "property": "value", "state": "doNotDisturb" } ],
      "screens": [ { "id": "main", "size": { "width": 1440, "height": 900 }, "root": {
        "id": "window_shell", "type": "Window",
        "properties": { "title": "Nyrqis Shell", "width": 1440, "height": 900 },
        "layout": { "x": 0, "y": 0, "width": 1440, "height": 900 },
        "events": {},
        "children": [ { "id": "toggle_dnd", "type": "Toggle",
                        "properties": { "value": false, "label": "Do not disturb" },
                        "layout": { "x": 848, "y": 8, "width": 224, "height": 32 },
                        "events": { "changed": "behavior_refresh" }, "children": [] } ]
      } } ]
    }"#;

    fn validate(text: &str) -> Result<(), String> {
        let raw: Value = serde_json::from_str(text).expect("test JSON must parse");
        validate_document(&raw)
    }

    #[test]
    fn valid_document_passes() {
        assert!(validate(VALID_SHELL).is_ok());
    }

    #[test]
    fn rejects_wrong_schema_version() {
        let text = VALID_SHELL.replace("\"version\": \"0.4.0\"", "\"version\": \"0.3.0\"");
        let err = validate(&text).unwrap_err();
        assert!(err.contains("unsupported schema version '0.3.0'"), "{err}");
    }

    #[test]
    fn rejects_unknown_component_type() {
        let text = VALID_SHELL.replace("\"type\": \"Toggle\"", "\"type\": \"BogusWidget\"");
        let err = validate(&text).unwrap_err();
        assert!(err.contains("unknown type 'BogusWidget'"), "{err}");
    }

    #[test]
    fn rejects_unknown_event() {
        let text = VALID_SHELL.replace("\"changed\": \"behavior_refresh\"", "\"hovered\": \"behavior_refresh\"");
        let err = validate(&text).unwrap_err();
        assert!(err.contains("event 'hovered' not in the 'Toggle' contract"), "{err}");
    }

    #[test]
    fn rejects_dangling_behavior_reference() {
        let text = VALID_SHELL.replace(
            "\"changed\": \"behavior_refresh\"",
            "\"changed\": \"behavior_missing\"");
        let err = validate(&text).unwrap_err();
        assert!(err.contains("unknown behavior 'behavior_missing'"), "{err}");
    }

    #[test]
    fn rejects_unknown_system_action() {
        let text = VALID_SHELL.replace("Nyrqis.Notification.Show", "Nyrqis.System.Shutdown");
        let err = validate(&text).unwrap_err();
        assert!(err.contains("unknown system action 'Nyrqis.System.Shutdown'"), "{err}");
    }

    #[test]
    fn rejects_unknown_action_argument() {
        let text = VALID_SHELL.replace("\"severity\": \"info\"", "\"severity\": \"info\", \"bogus\": 1");
        let err = validate(&text).unwrap_err();
        assert!(err.contains("argument 'bogus' not in the 'Nyrqis.Notification.Show' contract"), "{err}");
    }

    #[test]
    fn rejects_dangling_binding() {
        let text = VALID_SHELL.replace("\"component\": \"toggle_dnd\"", "\"component\": \"ghost\"");
        let err = validate(&text).unwrap_err();
        assert!(err.contains("component 'ghost' does not exist"), "{err}");
    }

    #[test]
    fn rejects_unknown_condition_state() {
        let text = VALID_SHELL.replace(
            "\"condition\": null",
            "\"condition\": { \"state\": \"nope\", \"operator\": \"equals\", \"value\": true }");
        let err = validate(&text).unwrap_err();
        assert!(err.contains("condition references unknown state 'nope'"), "{err}");
    }
}
