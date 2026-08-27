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
//! NFS-001 §9: a document must declare `version == "1.0.0"` (this
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

mod nexpr;

use serde::Deserialize;
use serde_json::{Map, Value};
use std::cell::RefCell;
use std::ffi::c_char;
use std::ptr;
use std::sync::OnceLock;

// ---------------------------------------------------------------------------
// Contract tables — from the Nyrqis API Registry (NFS-006 / ADR-0025)
// ---------------------------------------------------------------------------

const SUPPORTED_SCHEMA_VERSION: &str = "1.0.0";

const ABI_VERSION: u32 = 0x0001_0000;

// Status codes (negative i32, outside the errno range 1..=4095).
const ERR_INVALID_UTF8: i32 = -1;
const ERR_MALFORMED_JSON: i32 = -2;
const ERR_VERSION: i32 = -3;
const ERR_VALIDATION: i32 = -4;
const ERR_INTERNAL: i32 = -4096;

/// The Nyrqis API Registry — one property metadata entry (NFS-006:
/// name/type/default/bindable/required, plus optional min/max/
/// enumValues/units). The validator only needs the name; the rest is
/// carried for Inspector/editor consumers.
#[derive(Debug, Deserialize)]
struct PropertyDefinition {
    name: String,
    #[allow(dead_code)]
    #[serde(rename = "type", default)]
    property_type: String,
    #[allow(dead_code)]
    #[serde(default)]
    default: Option<serde_json::Value>,
    #[allow(dead_code)]
    #[serde(default)]
    bindable: bool,
    #[allow(dead_code)]
    #[serde(default)]
    required: bool,
    #[allow(dead_code)]
    #[serde(default)]
    min: Option<i64>,
    #[allow(dead_code)]
    #[serde(default)]
    max: Option<i64>,
    #[allow(dead_code)]
    #[serde(default)]
    enum_values: Option<Vec<String>>,
    #[allow(dead_code)]
    #[serde(default)]
    units: Option<String>,
}

/// The Nyrqis API Registry — one component entry.
#[derive(Debug, Deserialize)]
struct ComponentContract {
    #[serde(rename = "type")]
    type_name: String,
    #[allow(dead_code)]
    category: String,
    properties: Vec<PropertyDefinition>,
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

    // State scopes (NUI-SCHEMA §8.4): every scope name must be one of
    // the known scopes and hold an object. References are dotted
    // (`state.persistent.theme`); `global` is the named form of the
    // flat `states` section. Mirror of the floor's `_state_known`.
    const STATE_SCOPES: [&str; 5] =
        ["component", "global", "persistent", "screen", "session"];
    let state_scopes: Option<&Map<String, Value>> =
        raw.get("stateScopes").and_then(Value::as_object);
    if let Some(scopes) = state_scopes {
        for (scope_name, table) in scopes {
            if !STATE_SCOPES.contains(&scope_name.as_str()) {
                return Err(format!("stateScopes: unknown scope '{scope_name}'"));
            }
            if !table.is_object() {
                return Err(format!(
                    "stateScopes: scope '{scope_name}' must be an object"
                ));
            }
        }
    }

    // Localization (NUI-SCHEMA §8.1): resolve $localize: refs against
    // the ACTIVE locale's table; a missing key is a validation error
    // (fail-closed, byte-identical to the floor). No locales section =
    // no $localize: refs allowed anywhere.
    let locale_keys: Option<(&str, &Map<String, Value>)> =
        match raw.get("locales").and_then(Value::as_object) {
            None => None,
            Some(locales) => {
                let active = locales
                    .get("active")
                    .and_then(Value::as_str)
                    .ok_or_else(|| {
                        "locales section must declare a string 'active' and a 'tables' object"
                            .to_string()
                    })?;
                let tables = locales
                    .get("tables")
                    .and_then(Value::as_object)
                    .ok_or_else(|| {
                        "locales section must declare a string 'active' and a 'tables' object"
                            .to_string()
                    })?;
                for (name, table) in tables {
                    let ok = matches!(table, Value::Object(m) if m.values().all(Value::is_string));
                    if !ok {
                        return Err(format!(
                            "locale '{name}' table must map string keys to string values"
                        ));
                    }
                }
                let table = tables
                    .get(active)
                    .and_then(Value::as_object)
                    .ok_or_else(|| {
                        format!("locales: active locale '{active}' has no table")
                    })?;
                Some((active, table))
            }
        };

    // Resources (NUI-SCHEMA §8.2): unique ids, an allowed kind, a
    // non-empty path, and an optional 64-char hex sha256.
    const ASSET_KINDS: [&str; 8] =
        ["audio", "font", "icon", "image", "material", "svg", "video", "animation"];
    let asset_ids: Vec<String> = match raw.get("resources").and_then(Value::as_object) {
        None => Vec::new(),
        Some(resources) => {
            let assets = resources
                .get("assets")
                .and_then(Value::as_array)
                .ok_or_else(|| "resources section must declare an 'assets' list".to_string())?;
            let mut ids: Vec<String> = Vec::new();
            for asset in assets {
                let aid = asset
                    .get("id")
                    .and_then(Value::as_str)
                    .ok_or_else(|| "resource entries must declare a string 'id'".to_string())?;
                if ids.iter().any(|i| i == aid) {
                    return Err(format!("duplicate resource id '{aid}'"));
                }
                ids.push(aid.to_string());
                let kind = asset.get("kind").and_then(Value::as_str);
                if let Some(kind) = kind {
                    if !ASSET_KINDS.contains(&kind) {
                        return Err(format!(
                            "resource '{aid}': kind '{kind}' not in {ASSET_KINDS:?}"
                        ));
                    }
                } else {
                    return Err(format!("resource '{aid}': kind must be one of {ASSET_KINDS:?}"));
                }
                let path = asset.get("path").and_then(Value::as_str);
                if path.is_none_or(|p| p.is_empty()) {
                    return Err(format!("resource '{aid}': must declare a non-empty 'path'"));
                }
                if let Some(sha) = asset.get("sha256").and_then(Value::as_str) {
                    let ok = sha.len() == 64
                        && sha.chars().all(|c| c.is_ascii_hexdigit());
                    if !ok {
                        return Err(format!(
                            "resource '{aid}': 'sha256' must be a 64-char hex string"
                        ));
                    }
                }
            }
            ids
        }
    };

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

    // Pass 2.5: animations (NUI-SCHEMA §8.3) — unique ids, targets that
    // name components, non-empty properties, and validated timing.
    const ANIM_EASINGS: [&str; 5] =
        ["ease-in", "ease-in-out", "ease-out", "linear", "steps"];
    const ANIM_DIRECTIONS: [&str; 3] = ["alternate", "forward", "reverse"];
    let mut animation_ids: Vec<String> = Vec::new();
    if let Some(animations) = raw.get("animations").and_then(Value::as_array) {
        for animation in animations {
            let aid = animation
                .get("id")
                .and_then(Value::as_str)
                .ok_or_else(|| "animation entries must declare a string 'id'".to_string())?;
            if animation_ids.iter().any(|a| a == aid) {
                return Err(format!("duplicate animation id '{aid}'"));
            }
            animation_ids.push(aid.to_string());
            let target = animation.get("target").and_then(Value::as_str);
            if let Some(target) = target {
                if !component_ids.iter().any(|c| c == target) {
                    return Err(format!(
                        "animation '{aid}': target '{target}' does not exist"
                    ));
                }
            }
            let property = animation.get("property").and_then(Value::as_str);
            if property.is_none_or(|p| p.is_empty()) {
                return Err(format!("animation '{aid}': must declare a 'property'"));
            }
            for key in ["duration", "delay", "repeat"] {
                if let Some(value) = animation.get(key) {
                    let ok = matches!(value, Value::Number(n) if n.is_i64() && n.as_i64().unwrap_or(-1) >= 0);
                    if !ok {
                        return Err(format!(
                            "animation '{aid}': '{key}' must be a non-negative integer"
                        ));
                    }
                }
            }
            if let Some(easing) = animation.get("easing").and_then(Value::as_str) {
                if !ANIM_EASINGS.contains(&easing) {
                    return Err(format!(
                        "animation '{aid}': easing '{easing}' not in ['ease-in', 'ease-in-out', 'ease-out', 'linear', 'steps']"
                    ));
                }
            }
            if let Some(direction) = animation.get("direction").and_then(Value::as_str) {
                if !ANIM_DIRECTIONS.contains(&direction) {
                    return Err(format!(
                        "animation '{aid}': direction '{direction}' not in ['alternate', 'forward', 'reverse']"
                    ));
                }
            }
            // Keyframes (NUI-SCHEMA §8.3): optional multi-point curve —
            // each keyframe has a numeric offset in [0, 1] and a value,
            // and the offsets must be strictly increasing.
            if let Some(keyframes) = animation.get("keyframes") {
                if !keyframes.is_array() {
                    return Err(format!(
                        "animation '{aid}': keyframes must be a list"
                    ));
                }
                let mut prev_offset: Option<f64> = None;
                for (idx, kf) in keyframes.as_array().unwrap().iter().enumerate() {
                    if !kf.is_object() {
                        return Err(format!(
                            "animation '{aid}': keyframe {idx} must be an object"
                        ));
                    }
                    let offset = kf.get("offset").and_then(Value::as_f64);
                    let offset_ok = match offset {
                        Some(o) => (0.0..=1.0).contains(&o),
                        None => false,
                    };
                    if !offset_ok {
                        return Err(format!(
                            "animation '{aid}': keyframe {idx} 'offset' must be a number in [0, 1]"
                        ));
                    }
                    let offset = offset.unwrap();
                    if let Some(prev) = prev_offset {
                        if offset <= prev {
                            return Err(format!(
                                "animation '{aid}': keyframe {idx} 'offset' must be greater than the previous offset"
                            ));
                        }
                    }
                    prev_offset = Some(offset);
                    let value_ok = match kf.get("value") {
                        Some(v) => v.is_number() || v.is_string() || v.is_boolean(),
                        None => false,
                    };
                    if !value_ok {
                        return Err(format!(
                            "animation '{aid}': keyframe {idx} 'value' must be a number, string, or boolean"
                        ));
                    }
                }
            }
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

    // Pass 3: reusable-component masters (components[] — NFS-006 §9).
    // Master ids must be unique (and not collide with instance ids);
    // each master tree is validated like any component.
    let mut master_ids: Vec<String> = Vec::new();
    if let Some(masters) = raw.get("components").and_then(Value::as_array) {
        for master in masters {
            let id = master
                .get("id")
                .and_then(Value::as_str)
                .ok_or_else(|| "reusable components must declare a string 'id'".to_string())?;
            if master_ids.iter().any(|m| m == id) || component_ids.iter().any(|c| c == id) {
                return Err(format!("duplicate reusable component id '{id}'"));
            }
            master_ids.push(id.to_string());
            validate_component(master, &behavior_ids, &master_ids, raw, locale_keys,
                               &asset_ids, states, state_scopes)?;
        }
    }

    // Pass 4: components.
    if let Some(screens) = raw.get("screens").and_then(Value::as_array) {
        for screen in screens {
            if let Some(root) = screen.get("root") {
                validate_component(root, &behavior_ids, &master_ids, raw, locale_keys,
                                   &asset_ids, states, state_scopes)?;
            }
        }
    }

    // Pass 5: behaviors (full context).
    if let Some(behaviors) = raw.get("behaviors").and_then(Value::as_array) {
        for behavior in behaviors {
            validate_behavior(behavior, states, state_scopes, &component_ids, raw,
                              locale_keys, &animation_ids)?;
        }
    }

    // Pass 6: bindings.
    if let Some(bindings) = raw.get("bindings").and_then(Value::as_array) {
        for binding in bindings {
            validate_binding(binding, states, state_scopes, &component_ids, raw)?;
        }
    }

    Ok(())
}

fn validate_component(
    node: &Value,
    behavior_ids: &[String],
    master_ids: &[String],
    raw: &Value,
    locale_keys: Option<(&str, &Map<String, Value>)>,
    asset_ids: &[String],
    states: Option<&Map<String, Value>>,
    state_scopes: Option<&Map<String, Value>>,
) -> Result<(), String> {
    let id = node
        .get("id")
        .and_then(Value::as_str)
        .ok_or_else(|| "component nodes must declare a string 'id'".to_string())?;
    // Reusable-component instance (NFS-006 §9): the ref must name a
    // master in components[] and the contract is the master's type —
    // instances carry overrides (never a type/properties of their own).
    let reference = node.get("componentRef").and_then(Value::as_str);
    let (type_name, cc) = match reference {
        Some(reference) => {
            if node.get("type").is_some() {
                return Err(format!(
                    "component '{id}': reusable instance must not declare its own type"
                ));
            }
            if !master_ids.iter().any(|m| m == reference) {
                return Err(format!(
                    "component '{id}': componentRef '{reference}' does not name a reusable component"
                ));
            }
            let master_type = master_type_for(raw, reference)
                .ok_or_else(|| format!("component '{id}': componentRef '{reference}' has no type"))?;
            let cc = contract(master_type)
                .ok_or_else(|| format!("component '{id}': componentRef '{reference}' has unknown type '{master_type}'"))?;
            (master_type, cc)
        }
        None => {
            let type_name = node
                .get("type")
                .and_then(Value::as_str)
                .ok_or_else(|| format!("component '{id}' must declare a string 'type'"))?;
            let cc = contract(type_name)
                .ok_or_else(|| format!("component '{id}': unknown type '{type_name}'"))?;
            (type_name, cc)
        }
    };

    // Instances validate overrides; plain components validate properties.
    let prop_container = if reference.is_some() {
        node.get("overrides")
    } else {
        node.get("properties")
    };
    if let Some(container) = prop_container.and_then(Value::as_object) {
        let label = if reference.is_some() { "override" } else { "property" };
        // Universal properties accepted on every component type
        let universal = ["accessibility"];
        for key in container.keys() {
            if !cc.properties.iter().any(|p| p.name == *key)
                && !universal.contains(&key.as_str())
            {
                return Err(format!(
                    "component '{id}': {label} '{key}' not in the '{type_name}' contract"
                ));
            }
        }
    }

    if let Some(events_map) = node.get("events").and_then(Value::as_object) {
        for (event, behavior) in events_map {
            if !cc.events.iter().any(|e| e == event) {
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
        // Responsive constraints (NUI-SCHEMA §4): bounds and ratios are
        // validated identically to the floor (differential-tested).
        for key in ["x", "y", "width", "height", "minWidth", "maxWidth",
                    "minHeight", "maxHeight"]
        {
            let ok = match layout.get(key) {
                None => true,
                Some(Value::Number(n)) =>
                    n.is_i64() && n.as_i64().unwrap_or(-1) >= 0,
                Some(_) => false,
            };
            if !ok {
                return Err(format!(
                    "component '{id}': layout '{key}' must be a non-negative integer"
                ));
            }
        }
        for (lo_key, hi_key) in [("minWidth", "maxWidth"), ("minHeight", "maxHeight")] {
            let lo = layout.get(lo_key).and_then(Value::as_i64);
            let hi = layout.get(hi_key).and_then(Value::as_i64);
            if let (Some(lo), Some(hi)) = (lo, hi) {
                if lo > hi {
                    return Err(format!(
                        "component '{id}': layout '{lo_key}' must be <= '{hi_key}'"
                    ));
                }
            }
        }
        if let Some(ratio) = layout.get("aspectRatio") {
            let ok = matches!(ratio, Value::Number(n) if n.as_f64().unwrap_or(0.0) > 0.0);
            if !ok {
                return Err(format!(
                    "component '{id}': layout 'aspectRatio' must be a positive number"
                ));
            }
        }
        for key in ["anchorLeft", "anchorTop", "anchorRight", "anchorBottom"] {
            if let Some(flag) = layout.get(key) {
                if !flag.is_boolean() {
                    return Err(format!(
                        "component '{id}': layout '{key}' must be a boolean"
                    ));
                }
            }
        }
    }

    // Localization (NUI-SCHEMA §8.1): $localize: refs in properties
    // (instances: overrides) must exist in the active locale's table.
    if let Some((active, table)) = locale_keys {
        let container = if reference.is_some() {
            node.get("overrides")
        } else {
            node.get("properties")
        };
        if let Some(props) = container.and_then(Value::as_object) {
            let what = if reference.is_some() { "override" } else { "property" };
            for value in props.values() {
                check_localize_refs(
                    value, active, table,
                    &format!("component '{id}' {what}"))?;
            }
        }
    }

    // Resources (NUI-SCHEMA §8.2): $asset: refs must name a declared
    // resource (fail-closed, like the floor).
    {
        let container = if reference.is_some() {
            node.get("overrides")
        } else {
            node.get("properties")
        };
        if let Some(props) = container.and_then(Value::as_object) {
            let what = if reference.is_some() { "override" } else { "property" };
            for value in props.values() {
                check_asset_refs(
                    value, asset_ids, &format!("component '{id}' {what}"))?;
            }
        }
    }

    // Expressions (NUI-SCHEMA §7.2): whole-string `$expr:` values in
    // properties (instances: overrides) must parse, use only known
    // functions with correct arity, and reference only declared states.
    {
        let container = if reference.is_some() {
            node.get("overrides")
        } else {
            node.get("properties")
        };
        if let Some(props) = container.and_then(Value::as_object) {
            let what = if reference.is_some() { "override" } else { "property" };
            let known = merged_state_keys(states, state_scopes);
            for value in props.values() {
                nexpr::check_expr_refs(
                    value, Some(&known), &format!("component '{id}' {what}"))?;
            }
        }
    }

    if let Some(children) = node.get("children").and_then(Value::as_array) {
        for child in children {
            validate_component(child, behavior_ids, master_ids, raw, locale_keys,
                               asset_ids, states, state_scopes)?;
        }
    }
    Ok(())
}

/// A ``$asset:id`` reference must name a declared resource (fail-closed
/// at the import gate, mirroring the floor).
fn check_asset_refs(
    value: &Value,
    asset_ids: &[String],
    where_: &str,
) -> Result<(), String> {
    let Some(text) = value.as_str() else {
        return Ok(());
    };
    if !text.contains("$asset:") {
        return Ok(());
    }
    if asset_ids.is_empty() {
        return Err(format!(
            "{where_}: '$asset:' reference requires a 'resources' section with an 'assets' list"
        ));
    }
    let mut rest = text;
    while let Some(pos) = rest.find("$asset:") {
        let after = &rest[pos + "$asset:".len()..];
        let key: String = after
            .chars()
            .take_while(|c| c.is_ascii_alphanumeric() || *c == '_' || *c == '.' || *c == '-')
            .collect();
        if !key.is_empty() && !asset_ids.iter().any(|a| a == &key) {
            return Err(format!(
                "{where_}: asset '{key}' is not declared in resources"
            ));
        }
        rest = after;
    }
    Ok(())
}

/// A ``$localize:key`` reference must exist in the active locale's table
/// (fail-closed at the import gate, mirroring the floor). Plain text and
/// non-string values pass through.
fn check_localize_refs(
    value: &Value,
    active: &str,
    table: &Map<String, Value>,
    where_: &str,
) -> Result<(), String> {
    let Some(text) = value.as_str() else {
        return Ok(());
    };
    if !text.contains("$localize:") {
        return Ok(());
    }
    let mut rest = text;
    while let Some(pos) = rest.find("$localize:") {
        let after = &rest[pos + "$localize:".len()..];
        let key: String = after
            .chars()
            .take_while(|c| c.is_ascii_alphanumeric() || *c == '_' || *c == '.' || *c == '-')
            .collect();
        if !key.is_empty() && !table.contains_key(&key) {
            return Err(format!(
                "{where_}: localize key '{key}' not in locale '{active}'"
            ));
        }
        rest = after;
    }
    Ok(())
}

/// Find a reusable master's type by id in the document's components[].
fn master_type_for<'v>(raw: &'v Value, id: &str) -> Option<&'v str> {
    raw.get("components").and_then(Value::as_array)?.iter().find_map(|master| {
        if master.get("id").and_then(Value::as_str) == Some(id) {
            master.get("type").and_then(Value::as_str)
        } else {
            None
        }
    })
}

/// A state reference exists when it is a flat key or a dotted
/// reference into a declared scope (NUI-SCHEMA §8.4) — mirror of the
/// floor's `_state_known`.
fn state_known(
    state_key: &str,
    states: Option<&Map<String, Value>>,
    state_scopes: Option<&Map<String, Value>>,
) -> bool {
    if state_key.is_empty() {
        return false;
    }
    if let Some(dot) = state_key.find('.') {
        let scope = &state_key[..dot];
        let rest = &state_key[dot + 1..];
        if let Some(scopes) = state_scopes {
            if let Some(table) = scopes.get(scope) {
                if let Some(map) = table.as_object() {
                    return map.contains_key(rest);
                }
            }
        }
        return false;
    }
    if states.is_some_and(|s| s.contains_key(state_key)) {
        return true;
    }
    if let Some(scopes) = state_scopes {
        if let Some(table) = scopes.get("global") {
            if let Some(map) = table.as_object() {
                return map.contains_key(state_key);
            }
        }
    }
    false
}

/// Every dotted `scope.key` name declared in stateScopes.
fn scoped_state_keys(state_scopes: Option<&Map<String, Value>>) -> Vec<String> {
    let mut keys = Vec::new();
    if let Some(scopes) = state_scopes {
        for (scope, table) in scopes {
            if let Some(map) = table.as_object() {
                for key in map.keys() {
                    keys.push(format!("{scope}.{key}"));
                }
            }
        }
    }
    keys
}

fn validate_behavior(
    behavior: &Value,
    states: Option<&serde_json::Map<String, Value>>,
    state_scopes: Option<&Map<String, Value>>,
    component_ids: &[String],
    raw: &Value,
    locale_keys: Option<(&str, &Map<String, Value>)>,
    animation_ids: &[String],
) -> Result<(), String> {
    let id = behavior
        .get("id")
        .and_then(Value::as_str)
        .ok_or_else(|| "behavior entries must declare a string 'id'".to_string())?;

    // Condition — a leaf (expression or the legacy state/operator/value
    // equality form) or an AND/OR logic group (NUI-SCHEMA §7.3),
    // recursively. Null = always runs.
    if let Some(condition) = behavior.get("condition") {
        if !condition.is_null() {
            validate_condition(id, "", condition, states, state_scopes)?;
        }
    }

    // Action — exactly one of `action` (single) / `actions` (a non-empty
    // chain run in order). Each entry validates like a single action.
    let single = behavior.get("action");
    let chain = behavior.get("actions");
    match (single, chain) {
        (Some(_), Some(_)) => {
            return Err(format!(
                "behavior '{id}': must declare either 'action' or 'actions', not both"
            ));
        }
        (None, None) => {
            return Err(format!(
                "behavior '{id}' must declare an 'action' or 'actions'"
            ));
        }
        (Some(action), None) => {
            validate_behavior_action(
                id, action, states, state_scopes, raw, locale_keys, animation_ids)?;
        }
        (None, Some(actions)) => {
            let list = actions.as_array().ok_or_else(|| {
                format!("behavior '{id}': 'actions' must be a non-empty list")
            })?;
            if list.is_empty() {
                return Err(format!(
                    "behavior '{id}': 'actions' must be a non-empty list"
                ));
            }
            for action in list {
                if !action.is_object()
                    || action.get("name").and_then(Value::as_str).is_none()
                {
                    return Err(format!(
                        "behavior '{id}': each 'actions' entry must declare a 'name'"
                    ));
                }
                validate_behavior_action(
                    id, action, states, state_scopes, raw, locale_keys, animation_ids)?;
            }
        }
    }
    Ok(())
}

/// Validate one condition dict — a leaf or an AND/OR logic group — with
/// byte-identical messages to the reference floor. `path` is the element
/// path inside groups ("", " 0", " 0.1") for nested conditions.
fn validate_condition(
    id: &str,
    path: &str,
    condition: &Value,
    states: Option<&Map<String, Value>>,
    state_scopes: Option<&Map<String, Value>>,
) -> Result<(), String> {
    if let Some(logic) = condition.get("logic") {
        let logic_text = logic.as_str().ok_or_else(|| {
            format!("behavior '{id}': condition{path} 'logic' must be 'and' or 'or'")
        })?;
        if logic_text != "and" && logic_text != "or" {
            return Err(format!(
                "behavior '{id}': condition{path} 'logic' must be 'and' or 'or'"
            ));
        }
        let conditions = condition.get("conditions").and_then(Value::as_array)
            .ok_or_else(|| {
                format!("behavior '{id}': condition{path} 'conditions' must be a non-empty list")
            })?;
        if conditions.is_empty() {
            return Err(format!(
                "behavior '{id}': condition{path} 'conditions' must be a non-empty list"
            ));
        }
        for (i, sub) in conditions.iter().enumerate() {
            if !sub.is_object() {
                return Err(format!(
                    "behavior '{id}': condition{path} {i} must be an object"
                ));
            }
            validate_condition(id, &format!("{path} {i}"), sub, states, state_scopes)?;
        }
        return Ok(());
    }
    // Leaf: expression conditions (NUI-SCHEMA §7.2) supersede the
    // legacy state/operator/value equality form.
    if let Some(expression) = condition.get("expression") {
        let expr_text = expression.as_str().ok_or_else(|| {
            format!("behavior '{id}': condition{path} expression must be a string")
        })?;
        let known = merged_state_keys(states, state_scopes);
        nexpr::validate_expr(expr_text, Some(&known)).map_err(|err| {
            format!("behavior '{id}' condition{path} expression: {err}")
        })?;
        return Ok(());
    }
    let state_key = condition
        .get("state")
        .and_then(Value::as_str)
        .ok_or_else(|| {
            format!("behavior '{id}': condition{path} must declare a 'state'")
        })?;
    if !state_known(state_key, states, state_scopes) {
        return Err(format!(
            "behavior '{id}': condition{path} references unknown state '{state_key}'"
        ));
    }
    let operator = condition
        .get("operator")
        .and_then(Value::as_str)
        .ok_or_else(|| {
            format!("behavior '{id}': condition{path} must declare an 'operator'")
        })?;
    if operator != "equals" && operator != "notEquals" {
        return Err(format!(
            "behavior '{id}': condition{path} operator must be 'equals' or 'notEquals'"
        ));
    }
    Ok(())
}

/// Validate one action dict — the single `action` or one entry of an
/// `actions` chain — against the component/system contracts, including
/// $localize: and $expr: references in its arguments.
fn validate_behavior_action(
    id: &str,
    action: &Value,
    states: Option<&Map<String, Value>>,
    state_scopes: Option<&Map<String, Value>>,
    raw: &Value,
    locale_keys: Option<(&str, &Map<String, Value>)>,
    animation_ids: &[String],
) -> Result<(), String> {
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
                // Animations (NUI-SCHEMA §8.3): the reference must name
                // a declared animation — fail-closed like the floor.
                if name == "Nyrqis.Animation.Play" {
                    let anim_id = action
                        .get("arguments")
                        .and_then(|a| a.get("animation"))
                        .and_then(Value::as_str);
                    if anim_id.is_none_or(|a| !animation_ids.iter().any(|id| id == a)) {
                        let shown = anim_id.unwrap_or("");
                        return Err(format!(
                            "behavior '{id}': animation '{shown}' is not declared in 'animations'"
                        ));
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

    // Localization (NUI-SCHEMA §8.1): $localize: refs in action
    // arguments must exist in the active locale's table.
    if let Some((active, table)) = locale_keys {
        if let Some(args) = action.get("arguments").and_then(Value::as_object) {
            for value in args.values() {
                check_localize_refs(
                    value, active, table,
                    &format!("behavior '{id}' argument"))?;
            }
        }
    }

    // Expressions (NUI-SCHEMA §7.2): $expr: values in action arguments.
    if let Some(args) = action.get("arguments").and_then(Value::as_object) {
        let known = merged_state_keys(states, state_scopes);
        for value in args.values() {
            nexpr::check_expr_refs(
                value, Some(&known), &format!("behavior '{id}' argument"))?;
        }
    }
    Ok(())
}

/// Flat state keys plus every dotted `scope.key` name (NUI-SCHEMA §8.4)
/// — the combined known-state set for expression validation.
fn merged_state_keys(
    states: Option<&Map<String, Value>>,
    state_scopes: Option<&Map<String, Value>>,
) -> Map<String, Value> {
    let mut merged = Map::new();
    if let Some(states) = states {
        for (key, value) in states {
            merged.insert(key.clone(), value.clone());
        }
    }
    for key in scoped_state_keys(state_scopes) {
        merged.entry(key).or_insert(Value::Null);
    }
    merged
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
    state_scopes: Option<&Map<String, Value>>,
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
    if !state_known(state, states, state_scopes) {
        return Err(format!("binding: state '{state}' does not exist"));
    }
    if let Some(node) = find_component_in_doc(raw, component) {
        let type_name = node
            .get("type")
            .and_then(Value::as_str)
            .ok_or_else(|| format!("binding: component '{component}' has no type"))?;
        let properties = contract(type_name).map(|c| c.properties.as_slice()).unwrap_or(&[]);
        if !properties.iter().any(|p| p.name == property) {
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
      "version": "1.0.0",
      "project": { "name": "Nyrqis Shell" },
      "states": { "doNotDisturb": false, "lastRefresh": "12:04" },
      "animations": [
        { "id": "fade_in", "target": "toggle_dnd", "property": "opacity",
          "duration": 200, "easing": "ease-out",
          "keyframes": [ { "offset": 0.0, "value": 0.0 },
                           { "offset": 1.0, "value": 1.0 } ] }
      ],
      "behaviors": [
        { "id": "behavior_refresh", "condition": null,
          "action": { "target": "System", "name": "Nyrqis.Animation.Play",
                      "arguments": { "animation": "fade_in" } } }
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
        let text = VALID_SHELL.replace("\"version\": \"1.0.0\"", "\"version\": \"0.3.0\"");
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
        let text = VALID_SHELL.replace("Nyrqis.Animation.Play", "Nyrqis.System.Shutdown");
        let err = validate(&text).unwrap_err();
        assert!(err.contains("unknown system action 'Nyrqis.System.Shutdown'"), "{err}");
    }

    #[test]
    fn rejects_unknown_action_argument() {
        let text = VALID_SHELL.replace("\"animation\": \"fade_in\"", "\"animation\": \"fade_in\", \"bogus\": 1");
        let err = validate(&text).unwrap_err();
        assert!(err.contains("argument 'bogus' not in the 'Nyrqis.Animation.Play' contract"), "{err}");
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

    #[test]
    fn rejects_out_of_range_keyframe_offset() {
        let text = VALID_SHELL.replace("\"offset\": 0.0", "\"offset\": 1.5");
        let err = validate(&text).unwrap_err();
        assert!(err.contains("keyframe 0 'offset' must be a number in [0, 1]"), "{err}");
    }

    #[test]
    fn rejects_non_increasing_keyframe_offsets() {
        let text = VALID_SHELL.replace("\"offset\": 1.0", "\"offset\": 0.0");
        let err = validate(&text).unwrap_err();
        assert!(err.contains("keyframe 1 'offset' must be greater than the previous offset"), "{err}");
    }

    #[test]
    fn rejects_keyframe_without_value() {
        let text = VALID_SHELL.replace(
            "\"offset\": 1.0, \"value\": 1.0", "\"offset\": 1.0");
        let err = validate(&text).unwrap_err();
        assert!(err.contains("keyframe 1 'value' must be a number, string, or boolean"), "{err}");
    }

    #[test]
    fn rejects_both_action_and_actions() {
        let text = VALID_SHELL.replace(
            "\"arguments\": { \"animation\": \"fade_in\" } } }",
            "\"arguments\": { \"animation\": \"fade_in\" } },\n          \"actions\": [ { \"target\": \"System\", \"name\": \"Nyrqis.Animation.Play\" } ] }");
        let err = validate(&text).unwrap_err();
        assert!(err.contains("must declare either 'action' or 'actions', not both"), "{err}");
    }

    #[test]
    fn rejects_invalid_condition_logic() {
        let text = VALID_SHELL.replace(
            "\"condition\": null",
            "\"condition\": { \"logic\": \"xor\", \"conditions\": [ { \"expression\": \"state.doNotDisturb == true\" } ] }");
        let err = validate(&text).unwrap_err();
        assert!(err.contains("condition 'logic' must be 'and' or 'or'"), "{err}");
    }

    #[test]
    fn accepts_logic_group_and_action_chain() {
        let text = VALID_SHELL
            .replace("\"condition\": null",
                "\"condition\": { \"logic\": \"and\", \"conditions\": [ { \"expression\": \"state.doNotDisturb == true\" } ] }")
            .replace(
                "\"action\": { \"target\": \"System\", \"name\": \"Nyrqis.Animation.Play\",\n                      \"arguments\": { \"animation\": \"fade_in\" } }",
                "\"actions\": [ { \"target\": \"System\", \"name\": \"Nyrqis.Animation.Play\",\n                          \"arguments\": { \"animation\": \"fade_in\" } } ]");
        validate(&text).unwrap();
    }
}
