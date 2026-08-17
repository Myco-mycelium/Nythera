//! Nyrqis NUI expression language — Rust mirror (NUI-SCHEMA §7.2).
//!
//! The deterministic expression language used by `$expr:` values and
//! condition `expression` fields. This is a byte-for-byte behavioral
//! mirror of the Python reference floor (`ui/nexpr.py`): same grammar,
//! same precedence, and identical error messages (byte offsets included)
//! so the conformance gate can differential-test the two parsers.
//!
//! Grammar (lowest to highest precedence):
//!   or      := and ('||' and)*
//!   and     := compare ('&&' compare)*
//!   compare := add (('=='|'!='|'<'|'<='|'>'|'>=') add)*
//!   add     := mul (('+'|'-') mul)*
//!   mul     := unary (('*'|'/'|'%') unary)*
//!   unary   := ('!'|'-'|'+') unary | primary
//!   primary := number | string | 'true' | 'false'
//!            | 'state' ('.' ident)* | '(' or ')' | func '(' args ')'
//!
//! Functions: `if(cond, a, b)`, `min(a, ...)`, `max(a, ...)`,
//! `contains(haystack, needle)`, `format(value, "{0}" | "{0:.2f}", ...)`.

use serde_json::{Map, Value};

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

/// An expression error message, already prefixed with `expr:`.
fn syntax_err(pos: usize, text: &str) -> String {
    format!("expr: syntax error at {pos}: unexpected token '{text}'")
}

// ---------------------------------------------------------------------------
// Tokenizer
// ---------------------------------------------------------------------------

#[derive(Clone, Copy, PartialEq)]
enum TokKind {
    Num,
    Str,
    Ident,
    Op,
    Eof,
}

#[derive(Clone)]
struct Token {
    kind: TokKind,
    text: String,
    pos: usize,
}

struct Tokenizer<'a> {
    src: &'a str,
    bytes: &'a [u8],
    index: usize,
}

impl<'a> Tokenizer<'a> {
    fn new(src: &'a str) -> Self {
        Tokenizer { src, bytes: src.as_bytes(), index: 0 }
    }

    fn next(&mut self) -> Result<Token, String> {
        let n = self.bytes.len();
        // Skip whitespace.
        while self.index < n {
            let b = self.bytes[self.index];
            if b == b' ' || b == b'\t' || b == b'\n' || b == b'\r' {
                self.index += 1;
            } else {
                break;
            }
        }
        if self.index >= n {
            return Ok(Token { kind: TokKind::Eof, text: String::new(), pos: n });
        }
        let pos = self.index;
        let b = self.bytes[self.index];

        // Number literal: digits with an optional decimal part.
        if b.is_ascii_digit() {
            self.index += 1;
            while self.index < n && self.bytes[self.index].is_ascii_digit() {
                self.index += 1;
            }
            if self.index + 1 < n
                && self.bytes[self.index] == b'.'
                && self.bytes[self.index + 1].is_ascii_digit()
            {
                self.index += 1;
                while self.index < n && self.bytes[self.index].is_ascii_digit() {
                    self.index += 1;
                }
            }
            return Ok(Token {
                kind: TokKind::Num,
                text: self.src[pos..self.index].to_string(),
                pos,
            });
        }

        // String literal: "..." with \" \\ \n \t \r \0 escapes.
        if b == b'"' {
            self.index += 1;
            let mut text = String::new();
            loop {
                if self.index >= n {
                    return Err(syntax_err(pos, "\""));
                }
                let c = self.bytes[self.index];
                if c == b'"' {
                    self.index += 1;
                    break;
                }
                if c == b'\\' {
                    self.index += 1;
                    if self.index >= n {
                        return Err(syntax_err(pos, "\""));
                    }
                    match self.bytes[self.index] {
                        b'n' => text.push('\n'),
                        b't' => text.push('\t'),
                        b'r' => text.push('\r'),
                        b'"' => text.push('"'),
                        b'\\' => text.push('\\'),
                        b'0' => text.push('\0'),
                        other => text.push(other as char),
                    }
                    self.index += 1;
                } else {
                    // UTF-8: copy the whole multi-byte sequence.
                    let ch_len = utf8_len(c);
                    text.push_str(&self.src[self.index..self.index + ch_len]);
                    self.index += ch_len;
                }
            }
            return Ok(Token { kind: TokKind::Str, text, pos });
        }

        // Identifier.
        if b.is_ascii_alphabetic() || b == b'_' {
            self.index += 1;
            while self.index < n {
                let c = self.bytes[self.index];
                if c.is_ascii_alphanumeric() || c == b'_' {
                    self.index += 1;
                } else {
                    break;
                }
            }
            return Ok(Token {
                kind: TokKind::Ident,
                text: self.src[pos..self.index].to_string(),
                pos,
            });
        }

        // Operators (longest match first).
        const OPS: [&[u8]; 14] = [
            b"==", b"!=", b"<=", b">=", b"&&", b"||", b"(", b")", b"{", b"}",
            b"[", b"]", b",", b".",
        ];
        for op in OPS {
            let len = op.len();
            if self.index + len <= n && &self.bytes[self.index..self.index + len] == op {
                self.index += len;
                return Ok(Token {
                    kind: TokKind::Op,
                    text: String::from_utf8_lossy(op).to_string(),
                    pos,
                });
            }
        }
        // Single-character operators.
        const SINGLE: [u8; 9] = [b'!', b'<', b'>', b'=', b'+', b'-', b'*', b'/', b'%'];
        if SINGLE.contains(&b) {
            self.index += 1;
            return Ok(Token {
                kind: TokKind::Op,
                text: (b as char).to_string(),
                pos,
            });
        }

        Err(syntax_err(pos, &self.src[pos..pos + 1].to_string()))
    }
}

fn utf8_len(first: u8) -> usize {
    if first & 0x80 == 0 {
        1
    } else if first & 0xE0 == 0xC0 {
        2
    } else if first & 0xF0 == 0xE0 {
        3
    } else {
        4
    }
}

// ---------------------------------------------------------------------------
// AST
// ---------------------------------------------------------------------------

enum Node {
    Num(f64),
    Str(String),
    Bool(bool),
    StateRef(String),
    Func(String, Vec<Node>),
    Not(Box<Node>),
    Neg(Box<Node>),
    Bin(String, Box<Node>, Box<Node>),
}

// ---------------------------------------------------------------------------
// Parser
// ---------------------------------------------------------------------------

struct Parser {
    tokens: Vec<Token>,
    index: usize,
}

impl Parser {
    fn new(tokens: Vec<Token>) -> Self {
        Parser { tokens, index: 0 }
    }

    fn peek(&self) -> &Token {
        &self.tokens[self.index]
    }

    fn advance(&mut self) -> Token {
        let tok = self.tokens[self.index].clone();
        self.index += 1;
        tok
    }

    fn unexpected(&self, tok: &Token) -> String {
        syntax_err(tok.pos, &tok.text)
    }

    fn parse_or(&mut self) -> Result<Node, String> {
        let mut left = self.parse_and()?;
        while self.peek().text == "||" {
            self.advance();
            let right = self.parse_and()?;
            left = Node::Bin("||".to_string(), Box::new(left), Box::new(right));
        }
        Ok(left)
    }

    fn parse_and(&mut self) -> Result<Node, String> {
        let mut left = self.parse_compare()?;
        while self.peek().text == "&&" {
            self.advance();
            let right = self.parse_compare()?;
            left = Node::Bin("&&".to_string(), Box::new(left), Box::new(right));
        }
        Ok(left)
    }

    fn parse_compare(&mut self) -> Result<Node, String> {
        let mut left = self.parse_add()?;
        loop {
            let text = self.peek().text.clone();
            if matches!(text.as_str(), "==" | "!=" | "<" | "<=" | ">" | ">=") {
                self.advance();
                let right = self.parse_add()?;
                left = Node::Bin(text, Box::new(left), Box::new(right));
            } else {
                break;
            }
        }
        Ok(left)
    }

    fn parse_add(&mut self) -> Result<Node, String> {
        let mut left = self.parse_mul()?;
        loop {
            let text = self.peek().text.clone();
            if text == "+" || text == "-" {
                self.advance();
                let right = self.parse_mul()?;
                left = Node::Bin(text, Box::new(left), Box::new(right));
            } else {
                break;
            }
        }
        Ok(left)
    }

    fn parse_mul(&mut self) -> Result<Node, String> {
        let mut left = self.parse_unary()?;
        loop {
            let text = self.peek().text.clone();
            if text == "*" || text == "/" || text == "%" {
                self.advance();
                let right = self.parse_unary()?;
                left = Node::Bin(text, Box::new(left), Box::new(right));
            } else {
                break;
            }
        }
        Ok(left)
    }

    fn parse_unary(&mut self) -> Result<Node, String> {
        let text = self.peek().text.clone();
        if text == "!" {
            self.advance();
            let operand = self.parse_unary()?;
            return Ok(Node::Not(Box::new(operand)));
        }
        if text == "-" {
            self.advance();
            let operand = self.parse_unary()?;
            return Ok(Node::Neg(Box::new(operand)));
        }
        if text == "+" {
            self.advance();
            return self.parse_unary();
        }
        self.parse_primary()
    }

    fn parse_primary(&mut self) -> Result<Node, String> {
        let tok = self.advance();
        match tok.kind {
            TokKind::Num => {
                let value = tok.text.parse::<f64>().map_err(|_| {
                    syntax_err(tok.pos, &tok.text)
                })?;
                Ok(Node::Num(value))
            }
            TokKind::Str => Ok(Node::Str(tok.text)),
            TokKind::Ident => match tok.text.as_str() {
                "state" => {
                    let nxt = self.peek();
                    if nxt.text == "." {
                        self.advance();
                        let name_tok = self.advance();
                        if name_tok.kind != TokKind::Ident || name_tok.text == "state" {
                            return Err(self.unexpected(&name_tok));
                        }
                        let mut name = name_tok.text;
                        while self.peek().text == "." {
                            self.advance();
                            let seg = self.advance();
                            if seg.kind != TokKind::Ident {
                                return Err(self.unexpected(&seg));
                            }
                            name.push('.');
                            name.push_str(&seg.text);
                        }
                        Ok(Node::StateRef(name))
                    } else if nxt.text == "(" {
                        Err(syntax_err(nxt.pos, "'state' is not a function"))
                    } else {
                        Ok(Node::StateRef(String::new()))
                    }
                }
                "true" => Ok(Node::Bool(true)),
                "false" => Ok(Node::Bool(false)),
                name @ ("if" | "min" | "max" | "contains" | "format") => {
                    self.parse_call(name.to_string(), tok.pos)
                }
                name => Err(format!("expr: unknown function '{name}'")),
            },
            TokKind::Op if tok.text == "(" => {
                let node = self.parse_or()?;
                let closing = self.advance();
                if closing.text != ")" {
                    return Err(self.unexpected(&closing));
                }
                Ok(node)
            }
            _ => Err(self.unexpected(&tok)),
        }
    }

    fn parse_call(&mut self, name: String, _pos: usize) -> Result<Node, String> {
        let opening = self.advance();
        if opening.text != "(" {
            return Err(self.unexpected(&opening));
        }
        let mut args = Vec::new();
        if self.peek().text == ")" {
            self.advance();
            return Ok(Node::Func(name, args));
        }
        loop {
            args.push(self.parse_or()?);
            let sep = self.advance();
            if sep.text == ")" {
                break;
            }
            if sep.text != "," {
                return Err(self.unexpected(&sep));
            }
        }
        Ok(Node::Func(name, args))
    }
}

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

/// name -> (min_args, max_args) — mirrored from `ui/nexpr.py`.
const FUNCTIONS: [(&str, usize, Option<usize>); 5] = [
    ("if", 3, Some(3)),
    ("min", 2, None),
    ("max", 2, None),
    ("contains", 2, Some(2)),
    ("format", 2, None),
];

fn arity_err(name: &str, min_args: usize, max_args: Option<usize>, count: usize) -> String {
    let expected = match max_args {
        None => format!("at least {min_args}"),
        Some(max) if min_args == max => min_args.to_string(),
        Some(max) => format!("{min_args}-{max}"),
    };
    format!(
        "expr: function '{name}' expects {expected} argument(s), got {count}"
    )
}

/// Structural validation beyond syntax: known functions with correct
/// arity, and (when `known_states` is provided) only declared state
/// references. Mirrors `ui/nexpr.py`'s `validate()` — same first error.
fn validate_node<'a>(node: &'a Node, known_states: Option<&'a Map<String, Value>>) -> Option<String> {
    fn walk(node: &Node, known: Option<&Map<String, Value>>) -> Option<String> {
        match node {
            Node::Func(name, args) => {
                let sig = FUNCTIONS.iter().find(|(n, _, _)| n == name);
                let (min_args, max_args) = match sig {
                    Some((_, min, max)) => (*min, *max),
                    None => return Some(format!("expr: unknown function '{name}'")),
                };
                let count = args.len();
                if count < min_args || max_args.is_some_and(|m| count > m) {
                    return Some(arity_err(name, min_args, max_args, count));
                }
                for arg in args {
                    if let Some(problem) = walk(arg, known) {
                        return Some(problem);
                    }
                }
                None
            }
            Node::Not(operand) | Node::Neg(operand) => walk(operand, known),
            Node::Bin(_, left, right) => {
                walk(left, known).or_else(|| walk(right, known))
            }
            _ => None,
        }
    }

    // State references first (matches the floor's order: the floor
    // reports a missing state before walking functions).
    if let Some(states) = known_states {
        if let Some(name) = first_missing_state(node, states) {
            return Some(format!("expr: unknown state 'state.{name}'"));
        }
    }
    walk(node, known_states)
}

fn first_missing_state<'a>(node: &'a Node, states: &Map<String, Value>) -> Option<&'a str> {
    match node {
        Node::StateRef(name) => {
            if name.is_empty() {
                None
            } else if states.contains_key(name) {
                None
            } else {
                Some(name)
            }
        }
        Node::Func(_, args) => args.iter().find_map(|a| first_missing_state(a, states)),
        Node::Not(operand) | Node::Neg(operand) => first_missing_state(operand, states),
        Node::Bin(_, left, right) => {
            first_missing_state(left, states).or_else(|| first_missing_state(right, states))
        }
        _ => None,
    }
}

/// Validate an expression string (as it appears after a `$expr:` prefix
/// or in a condition's `expression` field). Returns `Ok(())` or the
/// first error, byte-identical to the Python floor.
pub fn validate_expr(text: &str, known_states: Option<&Map<String, Value>>) -> Result<(), String> {
    let mut tokenizer = Tokenizer::new(text);
    let mut tokens = Vec::new();
    loop {
        let tok = tokenizer.next()?;
        let is_eof = tok.kind == TokKind::Eof;
        tokens.push(tok);
        if is_eof {
            break;
        }
    }
    let mut parser = Parser::new(tokens);
    let node = parser.parse_or()?;
    if parser.peek().kind != TokKind::Eof {
        let tok = parser.peek();
        return Err(syntax_err(tok.pos, &tok.text));
    }
    if let Some(problem) = validate_node(&node, known_states) {
        return Err(problem);
    }
    Ok(())
}

/// Validate any `$expr:` values in the given JSON value (mirrors the
/// floor's `_check_expr_ref` — only whole-string `$expr:` values).
pub fn check_expr_refs(
    value: &Value,
    states: Option<&Map<String, Value>>,
    where_: &str,
) -> Result<(), String> {
    const PREFIX: &str = "$expr:";
    let Some(text) = value.as_str() else {
        return Ok(());
    };
    if !text.starts_with(PREFIX) {
        return Ok(());
    }
    validate_expr(&text[PREFIX.len()..], states)
        .map_err(|err| format!("{where_}: {err}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn states_map() -> Map<String, Value> {
        let value = json!({ "volume": 60, "dnd": false, "clockTime": "14:32" });
        value.as_object().unwrap().clone()
    }

    fn check(text: &str) -> Result<(), String> {
        validate_expr(text, Some(&states_map()))
    }

    #[test]
    fn valid_expressions_pass() {
        for text in [
            "state.dnd == true",
            "state.volume > 50 && !state.dnd",
            "if(state.volume > 50, \"loud\", \"quiet\")",
            "min(state.volume, 100)",
            "max(1, 2, 3)",
            "contains(\"hello\", \"ell\")",
            "format(state.clockTime, \"{0}\")",
            "format(state.volume, \"{0:.1f}\")",
            "1 + 2 * 3",
            "(state.volume - 10) * 2",
            "\"a\" + \"b\"",
        ] {
            assert!(check(text).is_ok(), "{text}: {:?}", check(text));
        }
    }

    #[test]
    fn syntax_errors_match_floor_positions() {
        // Positions are byte offsets in the expression text — these must
        // stay identical to the Python floor's tokenizer.
        assert_eq!(
            check("state.volume >").unwrap_err(),
            "expr: syntax error at 14: unexpected token ''"
        );
        assert_eq!(
            check("1 = 2").unwrap_err(),
            "expr: syntax error at 2: unexpected token '='"
        );
        assert_eq!(
            check("(1").unwrap_err(),
            "expr: syntax error at 2: unexpected token ''"
        );
        assert_eq!(
            check("state. > 1").unwrap_err(),
            "expr: syntax error at 7: unexpected token '>'"
        );
    }

    #[test]
    fn unknown_state_and_function_errors() {
        assert_eq!(
            check("state.ghost > 1").unwrap_err(),
            "expr: unknown state 'state.ghost'"
        );
        assert_eq!(
            check("bogus(state.volume)").unwrap_err(),
            "expr: unknown function 'bogus'"
        );
        assert_eq!(
            check("if(state.volume > 1)").unwrap_err(),
            "expr: function 'if' expects 3 argument(s), got 1"
        );
        assert_eq!(
            check("contains(\"a\", \"b\", \"c\")").unwrap_err(),
            "expr: function 'contains' expects 2 argument(s), got 3"
        );
    }

    #[test]
    fn check_expr_ref_only_whole_strings() {
        let states = states_map();
        assert!(check_expr_refs(&json!("plain text"), Some(&states), "where").is_ok());
        assert!(check_expr_refs(
            &json!("$expr:state.volume > 50"),
            Some(&states),
            "where"
        )
        .is_ok());
        assert_eq!(
            check_expr_refs(&json!("$expr:state.ghost"), Some(&states), "behavior 'b1' argument")
                .unwrap_err(),
            "behavior 'b1' argument: expr: unknown state 'state.ghost'"
        );
    }
}
