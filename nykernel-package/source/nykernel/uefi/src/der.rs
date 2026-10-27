//! Minimal DER/ASN.1 reader for the stub's trust path (the ADR-0014
//! authenticated half): enough of the encoding to walk an X.509
//! certificate and a PKCS#7/CMS SignedData with DEFINITE lengths --
//! which is everything a DER-encoded certificate contains (BER's
//! indefinite lengths are not DER and never appear in real
//! certificates or `openssl cms -outform DER` output). Hand-rolled
//! like every stub surface: zero dependencies, every parsing rule
//! appearing here is checkable by eye against ITU X.690.

/// A decoded TLV: its tag byte, the CONTENT octets, and the whole
/// encoding (tag+length+content) so digesting a certificate from the
/// wire is literally a slice.
pub struct Node<'a> {
    pub tag: u8,
    pub content: &'a [u8],
    /// The complete encoding, from the tag byte through the last
    /// content byte.
    pub raw: &'a [u8],
}

impl<'a> Node<'a> {
    pub fn is_seq(&self) -> bool {
        self.tag == 0x30
    }
    pub fn is_set(&self) -> bool {
        self.tag == 0x31
    }
    pub fn is_integer(&self) -> bool {
        self.tag == 0x02
    }
    pub fn is_oid(&self) -> bool {
        self.tag == 0x06
    }
    pub fn is_bitstring(&self) -> bool {
        self.tag == 0x03
    }
    pub fn is_octetstring(&self) -> bool {
        self.tag == 0x04
    }
    /// Context tag n, constructed (explicit/implicit wrappers: 0xA0+n).
    pub fn is_context(&self, n: u8) -> bool {
        self.tag == (0xA0 | n)
    }
    pub fn is_null(&self) -> bool {
        self.tag == 0x05 && self.content.is_empty()
    }
}

/// A position-based cursor over one DER blob.
pub struct Reader<'a> {
    pub buf: &'a [u8],
    pub pos: usize,
}

impl<'a> Reader<'a> {
    pub fn new(buf: &'a [u8]) -> Reader<'a> {
        Reader { buf, pos: 0 }
    }

    pub fn at_end(&self) -> bool {
        self.pos >= self.buf.len()
    }

    /// Reads one TLV. Refuses: running past the buffer, BER
    /// (indefinite, 0x80) lengths, or long-form lengths past 4 bytes --
    /// the three ways a DER stream can lie about its own size.
    pub fn next(&mut self) -> Result<Node<'a>, &'static str> {
        if self.pos >= self.buf.len() {
            return Err("DER: truncated at the tag byte");
        }
        let tag = self.buf[self.pos];
        let mut p = self.pos + 1;
        if p >= self.buf.len() {
            return Err("DER: truncated before the length");
        }
        let first = self.buf[p];
        p += 1;
        let len: usize;
        if first & 0x80 == 0 {
            len = first as usize;
        } else if first == 0x80 {
            return Err("DER: indefinite (BER) length -- indefinite lengths are not DER");
        } else {
            let n = (first & 0x7f) as usize;
            if n == 0 || n > 4 {
                return Err("DER: unsupported long-form length width");
            }
            if p + n > self.buf.len() {
                return Err("DER: truncated length bytes");
            }
            let mut acc: usize = 0;
            for i in 0..n {
                acc = (acc << 8) | self.buf[p + i] as usize;
            }
            p += n;
            len = acc;
        }
        if p + len > self.buf.len() {
            return Err("DER: content runs past the buffer");
        }
        let node = Node {
            tag,
            content: &self.buf[p..p + len],
            raw: &self.buf[self.pos..p + len],
        };
        self.pos = p + len;
        Ok(node)
    }

    /// Reads one TLV and refuses unless its tag is `tag` (a SEQUENCE of
    /// fixed-shape structures is exactly this, field by field).
    pub fn expect(&mut self, tag: u8, what: &'static str) -> Result<Node<'a>, &'static str> {
        let n = self.next()?;
        if n.tag != tag {
            return Err(what);
        }
        Ok(n)
    }

    /// Reads remaining bytes as ONE TLV of tag `tag` (an SET whose
    /// content is one element: digestAlgorithms, signerInfos).
    pub fn expect_rest(&mut self, tag: u8, what: &'static str) -> Result<Node<'a>, &'static str> {
        if self.pos != 0 {
            let n = self.expect(tag, what)?;
            if self.pos != self.buf.len() {
                return Err(what);
            }
            Ok(n)
        } else {
            Err("DER: empty buffer (internal misuse)")
        }
    }
}

/// OID content bytes for the algorithms this stub knows by name.
/// DER OID content: first two arcs packed (40*a+b), then base-128.
pub const OID_RSA_ENCRYPTION: [u8; 9] = [0x2a, 0x86, 0x48, 0x86, 0xf7, 0x0d, 0x01, 0x01, 0x01];
pub const OID_SHA256: [u8; 9] = [0x60, 0x86, 0x48, 0x01, 0x65, 0x03, 0x04, 0x02, 0x01];
pub const OID_SIGNED_DATA: [u8; 9] = [0x2a, 0x86, 0x48, 0x86, 0xf7, 0x0d, 0x01, 0x07, 0x02];
pub const OID_DATA: [u8; 9] = [0x2a, 0x86, 0x48, 0x86, 0xf7, 0x0d, 0x01, 0x07, 0x01];
