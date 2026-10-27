//! Minimal X.509 + PKCS#7/CMS trust-path reader for the stub's
//! trusted-carrier check (the ADR-0014 chain's authenticated half).
//! ONE carrier shape is walked here, by design: the DETACHED,
//! attrless PKCS#7 SignedData that `tools/make_carrier.sh` produces
//! with `openssl cms -sign -binary -noattr -outform DER` -- detached
//! content (the carrier carries no payload; the payload is the
//! KERNEL IMAGE the stub already embeds) and no signedAttrs (the
//! carrying of CMS attribute sets is exactly what `-noattr` removes;
//! a carrier signed with them is refused, named, not walked).
//!
//! The ANCHOR is one RFC 5280 CA, pinned as bytes in the stub
//! (`trusted-root.der`); the carrier's embedded signer certificate is
//! either the anchor's own (a carrier the anchor's key directly
//! signs) or a LEAF the anchor's key signs -- verified by walking the
//! leaf's TBSCertificate sha256 under the anchor's RSA SPKI, the
//! one-hop chain. A CA-only root means newly signed LEAVES validate
//! WITHOUT rebuilding the stub: the stub pins the issuer, not every
//! signer.
//!
//! Hand-rolled like every stub surface: zero dependencies, every
//! parsing rule below is checkable by eye against ITU X.690 (the
//! TLV/length layer, already in `der.rs`) + RFC 5280 sections 4.1,
//! 4.2.1.3 (keyUsage) and 4.2.1.9 (basicConstraints) + RFC 5652
//! sections 5 and 9.3/9.4 (SignedData and the attrless signerInfo
//! whose signature is the PKCS#1 v1.5 signing of sha256(content) --
//! which is exactly what `rsa::verify_pkcs1_v1_5_sha256` checks).

use crate::der::{Node, Reader, OID_DATA, OID_RSA_ENCRYPTION, OID_SHA256, OID_SIGNED_DATA};
use crate::rsa;
use crate::sha256::sha256;

/// 1.2.840.113549.1.1.11 -- sha256WithRSAEncryption, the only
/// certificate signature algorithm this walk accepts.
const OID_SHA256_WITH_RSA: [u8; 9] = [0x2a, 0x86, 0x48, 0x86, 0xf7, 0x0d, 0x01, 0x01, 0x0b];

/// 2.5.29.19 -- basicConstraints.
const OID_BASIC_CONSTRAINTS: [u8; 3] = [0x55, 0x1d, 0x13];
/// 2.5.29.15 -- keyUsage.
const OID_KEY_USAGE: [u8; 3] = [0x55, 0x1d, 0x0f];

fn oids_equal(a: &[u8], b: &[u8]) -> bool {
    a.len() == b.len() && a == b
}

/// The AlgorithmIdentifier shape check used for every algorithm node
/// this walk must VET (never merely shape-skip): a SEQUENCE whose
/// first element is the expected OID.
fn check_alg(node: &Node, oid: &[u8], what: &'static str) -> Result<(), &'static str> {
    if node.tag != 0x30 {
        return Err(what);
    }
    let mut r = Reader::new(node.content);
    let o = r.next().map_err(|_| what)?;
    if !o.is_oid() || !oids_equal(o.content, oid) {
        return Err(what);
    }
    Ok(())
}

/// The content octets of one DER INTEGER, checked to be exactly
/// that. The node is taken by value: every lifetime below is the
/// BUFFER the node's bytes borrow from, so the parsed fields never
/// borrow this call's locals.
fn integer_value<'buf>(node: Node<'buf>, what: &'static str) -> Result<&'buf [u8], &'static str> {
    if !node.is_integer() || node.content.is_empty() {
        return Err(what);
    }
    Ok(node.content)
}

/// A big-endian positive integer of at most 4 content bytes: the
/// widest DER INTEGER this walk accepts as an RSA exponent. Anything
/// wider is refused here, not truncated.
fn u32_from_be(be: &[u8], what: &'static str) -> Result<u32, &'static str> {
    if be.is_empty() || be.len() > 4 {
        return Err(what);
    }
    let mut v: u32 = 0;
    for &b in be {
        v = (v << 8) | (b as u32);
    }
    Ok(v)
}

/// One RSA-2048 SPKI the way RFC 3279 section 2.3.1 encodes it:
/// SEQUENCE { AlgorithmIdentifier { OID rsaEncryption, NULL },
/// BIT STRING { SEQUENCE { INTEGER modulus, INTEGER exponent } } } --
/// the BIT STRING's first content byte is the UNUSED-BITS count and
/// is zero for a whole-byte key.
pub struct RsaSpki<'a> {
    pub modulus_be: &'a [u8],
    pub exponent: u32,
}

fn parse_rsa_spki<'buf>(spki: Node<'buf>) -> Result<RsaSpki<'buf>, &'static str> {
    if spki.tag != 0x30 {
        return Err("spki: subjectPublicKeyInfo is not a SEQUENCE");
    }
    let mut r = Reader::new(spki.content);
    let alg = r.expect(0x30, "spki: AlgorithmIdentifier SEQUENCE missing")?;
    check_alg(&alg, &OID_RSA_ENCRYPTION, "spki: algorithm is not rsaEncryption")?;
    // Optional NULL parameters: an omitted one is legal too (the next
    // node then IS the BIT STRING, so the first read is either a NULL
    // to skip or the subjectPublicKey itself).
    let next = r.next().map_err(|_| "spki: truncated after AlgorithmIdentifier")?;
    let keybits = if next.is_null() {
        r.next().map_err(|_| "spki: subjectPublicKey missing")?
    } else {
        next
    };
    if !keybits.is_bitstring() {
        return Err("spki: subjectPublicKey is not a BIT STRING");
    }
    if keybits.content.is_empty() || keybits.content[0] != 0 {
        return Err("spki: BIT STRING unused-bits byte is not 0");
    }
    let mut k = Reader::new(&keybits.content[1..]);
    let kseq = k.expect(0x30, "spki: key content is not a SEQUENCE")?;
    if !k.at_end() {
        return Err("spki: excess bytes after the key SEQUENCE");
    }
    let mut ki = Reader::new(kseq.content);
    let nm = ki.expect(0x02, "spki: modulus INTEGER missing")?;
    let ex = ki.expect(0x02, "spki: exponent INTEGER missing")?;
    if !ki.at_end() {
        return Err("spki: excess bytes after the exponent INTEGER");
    }
    // DER INTEGER carries ONE leading 0x00 exactly when the value's
    // top bit read negative (the rule Bn::from_be states); an RSA-2048
    // modulus always has it set, so strip the sign byte and hand
    // rsa.rs the 256 value bytes it refuses to widen itself.
    let modulus_be = match integer_value(nm, "spki: malformed modulus INTEGER")? {
        [0x00, rest @ ..] => rest,
        other => other,
    };
    let exp_be = integer_value(ex, "spki: malformed exponent INTEGER")?;
    if modulus_be.is_empty() {
        return Err("spki: modulus value bytes are empty");
    }
    Ok(RsaSpki {
        modulus_be,
        exponent: u32_from_be(exp_be, "spki: exponent INTEGER is too wide (or empty) for u32")?,
    })
}

/// The two X.509v3 extensions this walk VETS (everything else is
/// skipped: the stub is honest by not using what it does not parse).
#[derive(Clone, Copy, Default)]
pub struct CertFlags {
    /// basicConstraints was present, was critical-flagged, and
    /// asserted CA:TRUE.
    pub bc_present: bool,
    pub bc_critical: bool,
    pub bc_is_ca: bool,
    /// keyUsage was present, was critical-flagged, and which of the
    /// two named bits this chain cares about are set.
    pub ku_critical: bool,
    pub ku_key_cert_sign: bool,
    pub ku_digital_signature: bool,
}

/// Walks RFC 5280 Extensions ([3] SEQUENCE OF Extension) with the
/// custom shape rules the two vetted extensions obey:
///   Extension ::= SEQUENCE { extnID OID, critical BOOLEAN DEFAULT
///   FALSE (canonical DER: either absent or 0xFF -- an explicit FALSE
///   is refused), extnValue OCTET STRING }
/// basicConstraints: SEQUENCE { cA BOOLEAN DEFAULT FALSE [,
///   pathLenConstraint INTEGER] } -- an exact-pinned CA means cA
///   asserts TRUE and NOTHING else rides with it (pathLen present in
///   the anchor/leaf shape is a refusal).
/// keyUsage: BIT STRING -- the unused-bits byte is honored, unnamed
///   bits must be zero (RFC 5280 says the same), any named bit past
///   the value octets is a refusal, and a value wider than two
///   octets is a refusal.
fn walk_extensions(content: &[u8]) -> Result<CertFlags, &'static str> {
    let mut f = CertFlags::default();
    let mut r = Reader::new(content);
    let seq = r.expect(0x30, "extensions: not a SEQUENCE OF")?;
    if !r.at_end() {
        return Err("extensions: excess bytes after the SEQUENCE OF");
    }
    let mut e = Reader::new(seq.content);
    let mut bc_seen = false;
    let mut ku_seen = false;
    while !e.at_end() {
        let ext = e.expect(0x30, "extensions: entry is not an Extension SEQUENCE")?;
        let mut x = Reader::new(ext.content);
        let oid = x.expect(0x06, "extension: extnID OID missing")?;
        let next = x.next().map_err(|_| "extension: truncated after extnID")?;
        // The optional critical BOOLEAN: canonical X.690 means a
        // DEFAULT-FALSE BOOLEAN is either absent or written 0xFF. In
        // both branches exactly one more node is consumed here: the
        // BOOLEAN followed by extnValue, or extnValue itself.
        let (critical, value) = if next.tag == 0x01 && next.content.len() == 1 {
            if next.content[0] != 0xFF {
                return Err("extension: a critical BOOLEAN refers FALSE -- not canonical");
            }
            let v = x.next().map_err(|_| "extension: truncated after the critical flag")?;
            (true, v)
        } else {
            (false, next)
        };
        if value.tag != 0x04 {
            return Err("extension: extnValue is not an OCTET STRING");
        }
        if !x.at_end() {
            return Err("extension: excess bytes after extnValue");
        }

        if oids_equal(oid.content, &OID_BASIC_CONSTRAINTS) {
            if bc_seen {
                return Err("extensions: basicConstraints appears twice");
            }
            bc_seen = true;
            f.bc_critical = critical;
            let mut v = Reader::new(value.content);
            let bc = v.expect(0x30, "basicConstraints: extnValue is not a SEQUENCE")?;
            if !v.at_end() {
                return Err("basicConstraints: excess bytes after the SEQUENCE");
            }
            if bc.content.is_empty() {
                // cA DEFAULT FALSE: nothing asserted = not a CA.
                f.bc_is_ca = false;
            } else {
                let mut b = Reader::new(bc.content);
                let flag = b.expect(0x01, "basicConstraints: not BOOLEAN and not empty")?;
                if !b.at_end() {
                    return Err("basicConstraints: pathLenConstraint or trailing content is not part of this walk's pinned shape");
                }
                if flag.content == [0xFF] {
                    f.bc_is_ca = true;
                } else if flag.content == [0x00] {
                    f.bc_is_ca = false;
                } else {
                    return Err("basicConstraints: a BOOLEAN other than 00/FF is not a real BOOLEAN");
                }
            }
            f.bc_present = true;
        } else if oids_equal(oid.content, &OID_KEY_USAGE) {
            if ku_seen {
                return Err("extensions: keyUsage appears twice");
            }
            ku_seen = true;
            f.ku_critical = critical;
            let mut v = Reader::new(value.content);
            let ku = v.expect(0x03, "keyUsage: extnValue is not a BIT STRING")?;
            if !v.at_end() {
                return Err("keyUsage: excess bytes after the BIT STRING");
            }
            if ku.content.is_empty() {
                return Err("keyUsage: empty BIT STRING");
            }
            let unused = ku.content[0];
            let vals = &ku.content[1..];
            if unused > 7 {
                return Err("keyUsage: unused-bits byte is not 0..7");
            }
            if vals.is_empty() || vals.len() > 2 {
                return Err("keyUsage: value octets not in the 1..2 range");
            }
            // X.690 named bits are counted from the MOST significant bit
            // of the first value octet (checked against openssl's own
            // encodings: keyCertSign+cRLSign = 03 02 01 06,
            // digitalSignature = 03 02 07 80): bit i is
            // vals[i/8] & (0x80 >> i%8), and any bit past the named
            // range must be zero (the same rule RFC 5280 states).
            let total = vals.len() * 8;
            let named = total - unused as usize;
            for i in named..total {
                if vals[i / 8] & (0x80u8 >> (i % 8)) != 0 {
                    return Err("keyUsage: a bit past the named range is set");
                }
            }
            f.ku_key_cert_sign = vals[0] & 0x04 != 0;
            f.ku_digital_signature = vals[0] & 0x80 != 0;
        }
        // Anything else (subjectKeyIdentifier, authorityKeyIdentifier,
        // ...): skipped. The stub uses exactly what it vets and lies
        // about nothing else.
    }
    Ok(f)
}

/// The anchor as parsed: exactly the fields the stub PINS, nothing
/// else. A certificate's own signature exists HERE as data (leaf
/// chain verification needs it); the ANCHOR's own self-signature is
/// deliberately NOT verified: the stub embeds the anchor BYTES, so
/// byte possession is the pin, and a signature whose key would be
/// what the signature proves can add nothing.
pub struct Cert<'a> {
    /// The TBSCertificate: the WHOLE TLV (tag+length+content) -- the
    /// exact bytes RFC 5280 4.1.1.2 hashes when anyone verifies a
    /// certificate under an issuer key.
    pub tbs: &'a [u8],
    /// serialNumber's INTEGER content octets (as encoded).
    pub serial: &'a [u8],
    /// issuer Name: the WHOLE TLV (tag+length+content), so the
    /// SignerIdentifier cross-check compares full encodings.
    pub issuer: &'a [u8],
    /// The certificate's OWN signature value bytes (BIT STRING minus
    /// the unused-bits byte).
    pub signature: &'a [u8],
    /// SPKI extract.
    pub spki: RsaSpki<'a>,
    /// The two vetted extensions.
    pub exts: CertFlags,
}

fn alg_oid<'buf>(node: Node<'buf>, what: &'static str) -> Result<&'buf [u8], &'static str> {
    if node.tag != 0x30 {
        return Err(what);
    }
    let mut r = Reader::new(node.content);
    let o = r.next().map_err(|_| what)?;
    if !o.is_oid() {
        return Err(what);
    }
    Ok(o.content)
}

impl<'a> Cert<'a> {
    /// The ROOT's shape rule: an issuer, not a leaf.
    pub fn assert_anchor_shape(&self) -> Result<(), &'static str> {
        if !self.exts.bc_present || !self.exts.bc_critical || !self.exts.bc_is_ca {
            return Err("anchor: basicConstraints must be present, critical, and assert CA:TRUE");
        }
        if !self.exts.ku_critical || !self.exts.ku_key_cert_sign {
            return Err("anchor: keyUsage must be critical and set keyCertSign");
        }
        Ok(())
    }

    /// The LEAF's shape rule: a signer, not another issuer.
    pub fn assert_leaf_shape(&self) -> Result<(), &'static str> {
        if self.exts.bc_present && self.exts.bc_is_ca {
            return Err("leaf: basicConstraints asserts CA -- the root issues leaves, not the reverse");
        }
        if !self.exts.ku_critical || !self.exts.ku_digital_signature {
            return Err("leaf: keyUsage must be critical and include digitalSignature");
        }
        Ok(())
    }

    /// Walks RFC 5280 section 4.1 in field order, refusing at the
    /// first field that is not what an X.509 v3 RSA certificate
    /// encodes, with a name for every refusal.
    pub fn parse(cert_der: &'a [u8]) -> Result<Cert<'a>, &'static str> {
        let mut r = Reader::new(cert_der);
        let seq = r.expect(0x30, "cert: not a SEQUENCE (PEM instead of DER?)")?;
        if !r.at_end() {
            return Err("cert: trailing bytes after the certificate SEQUENCE");
        }
        let mut inner = Reader::new(seq.content);
        let tbs = inner.expect(0x30, "cert: tbsCertificate SEQUENCE missing")?;
        let outer_alg = inner.expect(0x30, "cert: signatureAlgorithm SEQUENCE missing")?;
        let outer_oid = alg_oid(outer_alg, "cert: signatureAlgorithm is malformed")?;
        let sv = inner.expect(0x03, "cert: signatureValue BIT STRING missing")?;
        if !inner.at_end() {
            return Err("cert: excess bytes after signatureValue");
        }
        if sv.content.is_empty() || sv.content[0] != 0x00 {
            return Err("cert: signatureValue BIT STRING unused-bits byte is not 0");
        }

        let mut tr = Reader::new(tbs.content);
        let first = tr.next().map_err(|_| "cert: tbs truncated at version/serialNumber")?;
        let serial_node = if first.is_context(0) {
            // [0] EXPLICIT version: content is one INTEGER, and the
            // stub's shape is v3 (2), the only version `openssl req
            // -x509` emits for a self-signed root with extensions.
            let mut vr = Reader::new(first.content);
            let vnum = vr.expect(0x02, "cert: version INTEGER missing")?;
            if !vr.at_end() {
                return Err("cert: excess bytes inside the version [0]");
            }
            if vnum.content.len() != 1 || vnum.content[0] != 2 {
                return Err("cert: version is not 3 (the stub only walks v3 anchors)");
            }
            tr.next().map_err(|_| "cert: serialNumber missing after version")?
        } else {
            first
        };
        let serial = integer_value(serial_node, "cert: serialNumber is not a non-empty INTEGER")?;
        let tbs_alg = tr.expect(0x30, "cert: tbs signature AlgorithmIdentifier missing")?;
        let tbs_oid = alg_oid(tbs_alg, "cert: tbs signatureAlgorithm is malformed")?;
        if !oids_equal(tbs_oid, outer_oid) {
            return Err("cert: the tbs and outer signature algorithms differ");
        }
        if !oids_equal(tbs_oid, &OID_SHA256_WITH_RSA) {
            return Err("cert: signature algorithm is not sha256WithRSAEncryption");
        }
        let issuer = tr.expect(0x30, "cert: issuer Name SEQUENCE missing")?;
        let _validity = tr.expect(0x30, "cert: validity SEQUENCE missing")?;
        let _subject = tr.expect(0x30, "cert: subject Name SEQUENCE missing")?;
        let spki = tr.expect(0x30, "cert: subjectPublicKeyInfo SEQUENCE missing")?;
        let exts = if !tr.at_end() {
            // Every X.509 v3 certificate's LAST possible member. The
            // stub vets basicConstraints and keyUsage (the chain's
            // issuer/leaf roles live there) and skips all others.
            let exts_tlv = tr.next().map_err(|_| "cert: unreadable TBS element after spki")?;
            if !exts_tlv.is_context(3) {
                return Err("cert: unrecognized TBS element after spki (not extensions [3])");
            }
            if !tr.at_end() {
                return Err("cert: more than one TBS element after spki");
            }
            walk_extensions(exts_tlv.content)?
        } else {
            CertFlags::default()
        };
        let spki_p = parse_rsa_spki(spki)?;
        Ok(Cert {
            tbs: tbs.raw,
            serial,
            issuer: issuer.raw,
            signature: &sv.content[1..],
            spki: spki_p,
            exts,
        })
    }
}

/// The signer side of the carrier, as parsed: the bytes this walk
/// needs to bind the SIGNATURE to the signed leaf.
pub struct SignedData<'a> {
    /// The certificate the carrier embeds (certificates [0]): FULL
    /// DER bytes. The chain pin runs against these bytes below.
    pub signer_cert_der: &'a [u8],
    /// SignerIdentifier's issuer Name (whole TLV) and serial INTEGER
    /// content; both must byte-equal the SIGNER certificate's.
    pub sid_issuer: &'a [u8],
    pub sid_serial: &'a [u8],
    /// PKCS#1 v1.5 signature bytes over sha256(content).
    pub signature: &'a [u8],
}

impl<'a> SignedData<'a> {
    /// Walks RFC 5652 section 5 (the carrier's ContentInfo) then
    /// section 9.3 (one attrless SignerInfo), refusing anything the
    /// carrier shape does not include, with a name per refusal.
    pub fn parse(carrier_der: &'a [u8]) -> Result<SignedData<'a>, &'static str> {
        let mut r = Reader::new(carrier_der);
        let ci = r.expect(0x30, "carrier: no ContentInfo SEQUENCE")?;
        if !r.at_end() {
            return Err("carrier: trailing bytes after the ContentInfo");
        }
        let mut cir = Reader::new(ci.content);
        let ct = cir.expect(0x06, "carrier: ContentInfo contentType is not an OID")?;
        if !oids_equal(ct.content, &OID_SIGNED_DATA) {
            return Err("carrier: ContentInfo contentType is not the signedData OID");
        }
        let sd = cir
            .expect(0xA0, "carrier: ContentInfo [0] (SignedData wrapper) missing")?;
        if !cir.at_end() {
            return Err("carrier: excess bytes after the ContentInfo's [0]");
        }
        let mut dr = Reader::new(sd.content);
        let sds = dr.expect(0x30, "carrier: SignedData body is not a SEQUENCE")?;
        if !dr.at_end() {
            return Err("carrier: excess bytes after the SignedData SEQUENCE");
        }

        let mut r = Reader::new(sds.content);
        let ver = r.expect(0x02, "signedData: version INTEGER missing")?;
        if ver.content.len() != 1 || (ver.content[0] != 1 && ver.content[0] != 3) {
            return Err("signedData: version is neither 1 nor 3");
        }
        let dset = r.expect(0x31, "signedData: digestAlgorithms SET missing")?;
        {
            let mut dr = Reader::new(dset.content);
            let mut any = false;
            while !dr.at_end() {
                let alg = dr.next().map_err(|_| "signedData: malformed digestAlgorithms entry")?;
                check_alg(&alg, &OID_SHA256, "signedData: a digestAlgorithms entry is not SHA-256")?;
                any = true;
            }
            if !any {
                return Err("signedData: digestAlgorithms SET is empty");
            }
        }
        let enc = r.expect(0x30, "signedData: encapContentInfo SEQUENCE missing")?;
        {
            let mut er = Reader::new(enc.content);
            let ect = er.expect(0x06, "encapContentInfo: contentType is not an OID")?;
            if !oids_equal(ect.content, &OID_DATA) {
                return Err("encapContentInfo: contentType is not the data OID");
            }
            if !er.at_end() {
                // eContent [0] present = the payload rides INSIDE the
                // carrier. The stub demands DETACHED: the bytes it
                // verifies are the kernel image it is about to load,
                // and there is no second copy to synchronize.
                return Err("encapContentInfo: eContent present -- the stub walks DETACHED carriers only (sign without -nodetach)");
            }
        }
        let certs = r
            .expect(0xA0, "signedData: certificates [0] (the signer's certificate) missing")?;
        let signer_cert_der = {
            let mut cr = Reader::new(certs.content);
            let c = cr.expect(0x30, "certificates: no certificate SEQUENCE")?;
            if !cr.at_end() {
                return Err("certificates: more than one certificate -- the stub walks a single-signer carrier only");
            }
            c.raw
        };
        // The [1] (CRLs) slot: absent by construction. If something
        // else lands there, the SET-walk below refuses it, not us.
        let sis = r.expect(0x31, "signedData: signerInfos SET missing")?;
        if !r.at_end() {
            return Err("signedData: excess bytes after signerInfos");
        }

        let mut si = Reader::new(sis.content);
        let signer_info = si.expect(0x30, "signerInfos: no SignerInfo SEQUENCE")?;
        if !si.at_end() {
            return Err("signerInfos: more than one SignerInfo");
        }
        let mut s = Reader::new(signer_info.content);
        let sver = s.expect(0x02, "signerInfo: version INTEGER missing")?;
        if sver.content.len() != 1 || sver.content[0] != 1 {
            return Err("signerInfo: version is not 1 (the stub walks the IssuerAndSerial signer form only)");
        }
        let sid = s.expect(0x30, "signerInfo: sid is not an IssuerAndSerial SEQUENCE")?;
        let mut ir = Reader::new(sid.content);
        let issuer = ir.expect(0x30, "sid: IssuerAndSerial issuer Name missing")?;
        let serial = ir.expect(0x02, "sid: IssuerAndSerial serial INTEGER missing")?;
        if !ir.at_end() {
            return Err("sid: excess bytes after IssuerAndSerial");
        }
        let dig_alg = s.expect(0x30, "signerInfo: digestAlgorithm missing")?;
        check_alg(&dig_alg, &OID_SHA256, "signerInfo: digestAlgorithm is not SHA-256")?;
        let next = s.next().map_err(|_| "signerInfo: truncated after digestAlgorithm")?;
        if next.is_context(0) {
            return Err("signerInfo: signedAttrs present -- the stub walks -noattr carriers only");
        }
        let sig_alg_node = next;
        let sig_alg_is_rsa =
            check_alg(&sig_alg_node, &OID_RSA_ENCRYPTION, "").is_ok();
        let sig_alg_is_sha256_with_rsa =
            check_alg(&sig_alg_node, &OID_SHA256_WITH_RSA, "").is_ok();
        if !(sig_alg_is_rsa || sig_alg_is_sha256_with_rsa) {
            return Err("signerInfo: signatureAlgorithm is neither rsaEncryption nor sha256WithRSAEncryption");
        }
        let sig = s.expect(0x04, "signerInfo: signature OCTET STRING missing")?;
        if !s.at_end() {
            return Err("signerInfo: excess bytes after the signature (unsignedAttrs?)");
        }
        Ok(SignedData {
            signer_cert_der,
            sid_issuer: issuer.raw,
            sid_serial: serial.content,
            signature: sig.content,
        })
    }
}

fn verify_content_signature(
    signer_spki: &RsaSpki,
    signer_issuer: &[u8],
    signer_serial: &[u8],
    sd: &SignedData,
    content: &[u8],
) -> Result<(), &'static str> {
    // The SignerIdentifier must name THE certificate the carrier
    // embeds: issuer and serial, encodings byte-equal.
    if sd.sid_issuer != signer_issuer {
        return Err("carrier: the SignerIdentifier's issuer does not byte-equal the signer certificate's issuer Name");
    }
    if sd.sid_serial != signer_serial {
        return Err("carrier: the SignerIdentifier's serial does not byte-equal the signer certificate's serialNumber");
    }
    let digest = sha256(content);
    rsa::verify_pkcs1_v1_5_sha256(sd.signature, signer_spki.modulus_be, signer_spki.exponent, &digest)
}

/// The one function `main` calls, with the three things it has at
/// boot: the embedded ANCHOR certificate DER (a CA), the embedded
/// CARRIER DER, and the kernel image bytes the carrier must be a
/// signature over. Refuses, named, on: a carrier that does not
/// parse, an anchor that is not a CA kyUsage/basicConstraints-wise,
/// an embedded signer that is neither the anchor itself nor
/// verifiable under the anchor's key (the one-hop chain), a leaf
/// carrying the wrong issuer role, a SignerInfo that does not tie to
/// that signer certificate, and then the RSA-2048 PKCS#1 v1.5
/// verification over sha256(content) itself.
pub fn verify_detached_signed_data<'r, 'c>(
    anchor_cert_der: &'r [u8],
    carrier_der: &'c [u8],
    content: &[u8],
) -> Result<(), &'static str> {
    let root = Cert::parse(anchor_cert_der)?;
    root.assert_anchor_shape()?;
    let sd = SignedData::parse(carrier_der)?;
    let signer_cert_bytes = sd.signer_cert_der;
    if signer_cert_bytes == anchor_cert_der {
        // A carrier signed DIRECTLY by the anchor's own key (the
        // single-cert shape `make_carrier.sh` used before the chain
        // walk; kept legal so old carriers cannot be waved in).
        verify_content_signature(&root.spki, root.issuer, root.serial, &sd, content)
    } else {
        // RFC 5280 one-hop chain: the embedded LEAF's TBSCertificate
        // sha256 must verify under the ANCHOR's key. This is the pin
        // that no carrier signed by a foreign unanchored key walks.
        let leaf = Cert::parse(signer_cert_bytes)?;
        let leaf_tbs_digest = sha256(leaf.tbs);
        rsa::verify_pkcs1_v1_5_sha256(
            leaf.signature,
            root.spki.modulus_be,
            root.spki.exponent,
            &leaf_tbs_digest,
        )
        .map_err(|_| "carrier: the embedded signer certificate is not the anchor and does not verify under the anchor's key (no chain to the root)")?;
        leaf.assert_leaf_shape()?;
        verify_content_signature(&leaf.spki, leaf.issuer, leaf.serial, &sd, content)
    }
}
