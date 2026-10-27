//! RSA-2048 PKCS#1 v1.5 verification for the stub's trusted-carrier
//! check (the ADR-0014 authenticated half). Hand-rolled like every
//! stub surface: a fixed-width 2048-bit bignum in 32-bit limbs, and
//! public-key modpow (e = 65537, the single exponent every real RSA
//! certificate carries) via Montgomery multiplication: textbook REDC
//! applied limb by limb -- at each limb i its own cancellation
//! multiplier m_i = t[i] * -n^{-1} mod 2^32 clears that one limb;
//! one global m for the whole n is the classic trap (it only
//! guarantees divisibility by 2^32, not by R -- caught by probe
//! before it ever reached a carrier). One conditional subtract
//! restores < n. Montgomery needs an ODD modulus -- an RSA modulus
//! is a product of two odd primes and always is, and a non-odd
//! modulus is refused by name rather than silently misreduced --
//! and operands < R = 2^2048, which the out-of-range check on the
//! signature already pins. The per-verify cost falls from the
//! bit-serial ~2*10^8 u32 operations (2048 doubling steps through a
//! 65-limb accumulator, minutes under TCG -- see the UEFI gate's
//! catalogued timeout) to ~10^6, well under a second. Verification
//! is a public-key operation: no CRT, no private-key arithmetic,
//! nothing secret-dependent to constant-time.

/// One 2048-bit value, limbs little-endian (limb 0 = the least
/// significant 32 bits). Exactly 64 limbs; the 65th accumulator limb
/// lives only inside the reduction routines.
const L: usize = 64;
/// The working accumulator width: 65 limbs so a shifted 2048-bit
/// value plus carry never loses a bit before conditional reduction.
const LA: usize = L + 1;

#[derive(Clone, Copy)]
pub struct Bn {
    pub limbs: [u32; L],
}

impl Bn {
    pub fn zero() -> Bn {
        Bn { limbs: [0u32; L] }
    }

    pub fn one() -> Bn {
        let mut v = Bn::zero();
        v.limbs[0] = 1;
        v
    }

    /// Big-endian bytes, most significant first. Bytes beyond the
    /// first 256 must be zeros (a DER INTEGER pads positive values
    /// with 0x00 only when the top bit would read negative -- all such
    /// padding is zero) or the value does not fit: refuse.
    pub fn from_be(a: &[u8]) -> Result<Bn, &'static str> {
        if a.len() > L * 4 {
            for &b in &a[..a.len() - L * 4] {
                if b != 0 {
                    return Err("rsa: value wider than 2048 bits");
                }
            }
        }
        let start = a.len().saturating_sub(L * 4);
        let tail = &a[start..];
        // Spread the low 256 bytes: byte j (from the right, 0 = LSB)
        // lives in limb j/4 at bit offset (j%4)*8.
        let mut v = Bn::zero();
        for (j, &b) in tail.iter().rev().enumerate() {
            if b == 0 {
                continue;
            }
            v.limbs[j / 4] |= (b as u32) << ((j % 4) * 8);
        }
        // A nonzero byte can never overflow a limb it exclusively owns
        // (each byte occupies disjoint bit ranges), so no carry check
        // is needed above.
        Ok(v)
    }

    pub fn cmp(&self, other: &Bn) -> core::cmp::Ordering {
        for i in (0..L).rev() {
            if self.limbs[i] != other.limbs[i] {
                return self.limbs[i].cmp(&other.limbs[i]);
            }
        }
        core::cmp::Ordering::Equal
    }

    pub fn is_ge(&self, other: &Bn) -> bool {
        self.cmp(other) != core::cmp::Ordering::Less
    }

    /// The i-th bit (i = 0 is the LSB).
    pub fn bit(&self, i: usize) -> bool {
        if i >= L * 32 {
            return false;
        }
        (self.limbs[i / 32] >> (i % 32)) & 1 == 1
    }
}

/// Compares a 65-limb accumulator against `n` (its limb L is 0).
fn acc_ge_n(acc: &[u32; LA], n: &Bn) -> bool {
    for j in (0..LA).rev() {
        let hl = acc[j];
        let nl = if j < L { n.limbs[j] } else { 0 };
        if hl != nl {
            return hl > nl;
        }
    }
    // Equal values count as >= (subtracting equal gives zero -- exact).
    true
}

fn acc_sub_n(acc: &mut [u32; LA], n: &Bn) {
    let mut borrow: u64 = 0;
    for j in 0..LA {
        let nl = if j < L { n.limbs[j] as u64 } else { 0 };
        let rhs = nl + borrow;
        if (acc[j] as u64) >= rhs {
            acc[j] = (acc[j] as u64 - rhs) as u32;
            borrow = 0;
        } else {
            acc[j] = ((acc[j] as u64 + (1 << 32)) - rhs) as u32;
            borrow = 1;
        }
    }
    debug_assert!(borrow == 0, "rsa: reduction borrow ran out of limbs");
}

/// The Montgomery modulus constant, precomputed once per verification:
/// n0inv = -n^{-1} mod 2^32 via Hensel lifting (Newton on x = n0^{-1}*:
/// x = x*(2 - n0*x) mod 2^32, two bits of accuracy per iteration from
/// the x=1 start, valid only because n is ODD -- gated at the caller).
/// Guaranteed-exact after six iterations (1,2,4,8,16,32,64 >= 32 bits).
fn mont_n0inv(n: &Bn) -> u32 {
    let n0 = n.limbs[0];
    let mut x: u32 = 1;
    for _ in 0..6 {
        x = x.wrapping_mul(2u32.wrapping_sub(n0.wrapping_mul(x)));
    }
    x.wrapping_neg()
}

/// Montgomery multiplication: (a * b * R^{-1}) mod n for a, b < n <
/// R = 2^2048 and n odd, over a 129-limb accumulator T. Phase 1 is
/// the exact schoolbook product T = a*b (128 limbs: every partial
/// sum is a prefix of a value < R^2, so nothing walks past limb
/// 127). Phase 2 is textbook REDC applied limb by limb: at each
/// limb i, m_i = t[i] * n0inv (mod 2^32) cancels exactly that limb
/// -- m_i * n[0] + t[i] = 0 (mod 2^32) by the n0inv construction --
/// and the m_i * n * 2^(32i) add pushes the limb's worth of value
/// upward; a fresh multiplier per limb is REQUIRED, because one
/// global m for the whole n makes T + m*n divisible only by 2^32,
/// not by R -- the wrong shape (caught by probe before any carrier
/// saw it). After all 64 limbs the low limbs are all zero, so
///   T >> R is exact and T >> R < n^2/R + n < 2n
/// (a*b < R, the cancellation sum < n*R, both divided by R), so ONE
/// conditional subtract restores < n. Per inner add the bound is
/// exact: t[j] < 2^32 (running values are prefixes of T < 2*R^2),
/// m*n[.] = (2^32-1)^2 < 2^64, carry < 2^32 -> the u64 sum < 2^64.
/// A third subtract names a broken invariant and refuses -- the
/// refuse-don't-trust style the rest of this module carries.
fn mont_mul(a: &Bn, b: &Bn, n: &Bn, n0inv: u32) -> Result<Bn, &'static str> {
    // T = a*b, 128 limbs of schoolbook accumulation.
    let mut t = [0u32; 2 * L + 1];
    for j in 0..L {
        let aj = a.limbs[j] as u64;
        let mut carry: u64 = 0;
        for i in 0..L {
            let cur = t[j + i] as u64 + aj * (b.limbs[i] as u64) + carry;
            t[j + i] = cur as u32;
            carry = cur >> 32;
        }
        // Walk the per-row carry upward; the running value is a prefix
        // of a product < 2^4096, so it cannot survive past limb 127.
        let mut k = j + L;
        while carry != 0 {
            debug_assert!(k < 2 * L, "rsa: schoolbook carry walked past the product");
            if k >= 2 * L {
                return Err("rsa: schoolbook carry walked past the product");
            }
            let cur = t[k] as u64 + carry;
            t[k] = cur as u32;
            carry = cur >> 32;
            k += 1;
        }
    }
    // Phase 2: per-limb REDC after the product phase.
    for i in 0..L {
        let m = t[i].wrapping_mul(n0inv);
        // Add m*n shifted i limbs: wraps the *current* limb i to zero
        // (m*n[0] + t[i] = 0 mod 2^32, exact by construction) and
        // carries the limb's worth of value upward.
        let mut c: u64 = 0;
        for j in i..(i + L) {
            let cur = t[j] as u64 + (m as u64) * (n.limbs[j - i] as u64) + c;
            t[j] = cur as u32;
            c = cur >> 32;
        }
        // The total stays < n^2 + n*R (a*b < n^2, and the sum of all
        // cancellation terms is < (R-1)*n < n*R) < 2*R^2 (see the doc
        // block), so the ripple cannot walk past limb 2L; a third
        // subtract's name-refusal guards the same invariant here.
        let mut k = i + L;
        while c != 0 {
            debug_assert!(k < t.len(), "rsa: REDC ripple walked past the accumulator");
            if k >= t.len() {
                return Err("rsa: REDC ripple walked past the accumulator");
            }
            let cur = t[k] as u64 + c;
            t[k] = cur as u32;
            c = cur >> 32;
            k += 1;
        }
    }
    for i in 0..L {
        debug_assert!(t[i] == 0, "rsa: REDC left a low limb nonzero");
        if t[i] != 0 {
            return Err("rsa: REDC left a low limb nonzero");
        }
    }
    // result = T >> R: the shift is EXACT (low limbs all zero), and
    // its value is < 2n. The 65th limb catches the one-bit range
    // where T crosses 2^4096.
    let mut acc = [0u32; LA];
    for i in 0..=L {
        acc[i] = t[L + i];
    }
    // One conditional subtract restores < n; a second is possible
    // only on the 65th-limb edge, a third names a broken invariant.
    let mut subs = 0;
    while acc_ge_n(&acc, n) {
        if subs == 2 {
            return Err("rsa: Montgomery reduction invariant broken (third subtract needed)");
        }
        acc_sub_n(&mut acc, n);
        subs += 1;
    }
    let mut out = Bn::zero();
    out.limbs.copy_from_slice(&acc[..L]);
    debug_assert!(acc[L] == 0, "rsa: Montgomery result kept a set 65th limb");
    if acc[L] != 0 {
        return Err("rsa: Montgomery result kept a set 65th limb");
    }
    Ok(out)
}

/// R^2 mod n, the Montgomery ENTRY multiplier, derived per
/// verification from R mod n by 2048 double-and-conditionally-
/// subtract steps: X -> (X*2) mod n via a 65-limb left shift (acc <
/// n -> 2*acc < 2n, so one conditional subtract restores < n, the
/// 65th limb catching the bit that walks over 2^2048). No shortcuts
/// apply: a bit-doubling is a factor of 2, and R is 2^2048, so the
/// full 2048 steps are what R^2 needs -- ~2.7*10^5 u32 operations,
/// a fraction of the chain's cost, and it stays bounded, refusing
/// by name on any invariant break.
fn mont_r2(n: &Bn, r_mod_n: &Bn) -> Result<Bn, &'static str> {
    let mut acc = [0u32; LA];
    acc[..L].copy_from_slice(&r_mod_n.limbs);
    for _ in 0..(L * 32) {
        let mut c = 0u32;
        for j in 0..LA {
            let v = ((acc[j] as u64) << 1) | (c as u64);
            acc[j] = v as u32;
            c = (v >> 32) as u32;
        }
        let mut subs = 0;
        while acc_ge_n(&acc, n) {
            debug_assert!(subs < 2, "rsa: R^2 doubling subtracted past the 2n bound");
            if subs >= 2 {
                return Err("rsa: R^2 doubling needs a second subtract (2n bound broken)");
            }
            acc_sub_n(&mut acc, n);
            subs += 1;
        }
    }
    let mut out = Bn::zero();
    out.limbs.copy_from_slice(&acc[..L]);
    Ok(out)
}

/// base^65537 mod n, Montgomery-domain all the way: enter via
/// mont_mul(base, R^2 mod n) (the classic to_mont conversion --
/// mont_mul(base, R mod n) is a numerical no-op, base*R*R^{-1} =
/// base, the exact trap: the entry factor must be R^2, derived via
/// mont_r2 above), run the 17-step MSB-first square-and-multiply
/// (65537 = 0b10000000000000001), leave via mont_mul(r, 1).
/// R mod n is free: for n in (2^2047, 2^2048) (the top-bit gate the
/// verifier holds) R mod n = R - n = the two's complement of n over
/// its full width.
fn modpow65537(base: &Bn, n: &Bn) -> Result<Bn, &'static str> {
    // Montgomery's Hensel multiplier needs n0 inverted mod 2^32, so
    // the modulus MUST be odd; an RSA modulus (odd primes product)
    // always is, and anything else is refused, never misreduced.
    if n.limbs[0] & 1 == 0 {
        return Err("rsa: modulus is even -- Montgomery multiplication requires an odd modulus");
    }
    let n0inv = mont_n0inv(n);
    // R mod n = (!n) + 1, two's complement across 64 limbs.
    let mut r_mod_n = Bn::zero();
    let mut carry = 1u32;
    for j in 0..L {
        let sum = (!n.limbs[j]) as u64 + carry as u64;
        r_mod_n.limbs[j] = sum as u32;
        carry = (sum >> 32) as u32;
    }
    // Enter the Montgomery domain: base_m = base * R mod n.
    let r2 = mont_r2(n, &r_mod_n)?;
    let base_m = mont_mul(base, &r2, n, n0inv)?;
    // r_m = R mod n represents ONE.
    let mut r_m = r_mod_n;
    // 65537 = 0b10000000000000001: MSB-first over 17 bits.
    for i in (0..17usize).rev() {
        r_m = mont_mul(&r_m, &r_m, n, n0inv)?;
        if (65537u32 >> i) & 1 == 1 {
            r_m = mont_mul(&r_m, &base_m, n, n0inv)?;
        }
    }
    // Leave the Montgomery domain: mont_mul(r_m, 1) = r_m * R^{-1},
    // the true residue.
    mont_mul(&r_m, &Bn::one(), n, n0inv)
}

/// RFC 8017 EMSA-PKCS1-v1_5 SHA-256 DigestInfo prefix:
/// || AlgId(30 31 30 0d 06 09 <sha256 oid> 05 00) || 04 20 ||.
const SHA256_DIGEST_INFO_PREFIX: [u8; 19] = [
    0x30, 0x31, 0x30, 0x0d, 0x06, 0x09, 0x60, 0x86, 0x48, 0x01, 0x65, 0x03, 0x04, 0x02, 0x01, 0x05,
    0x00, 0x04, 0x20,
];

/// Verifies an RSA-2048 PKCS#1 v1.5 signature over a SHA-256 message
/// digest: s^e mod n must decode as 00 01 FF..FF 00 DigestInfo digest,
/// with the RFC's mandated PS minimum (>= 8 FF bytes here realized as
/// the exact 202 the width forces) and SHA-256's AlgId. Every refusal
/// names which check failed; the boot dies on any of them, so there
/// is no caller silently swallowing reasons.
pub fn verify_pkcs1_v1_5_sha256(
    sig: &[u8],
    modulus_be: &[u8],
    exponent: u32,
    digest: &[u8; 32],
) -> Result<(), &'static str> {
    if exponent != 65537 {
        return Err("rsa: public exponent is not 65537");
    }
    if modulus_be.len() != 256 {
        return Err("rsa: modulus is not exactly 256 bytes wide");
    }
    if modulus_be[0] & 0x80 == 0 {
        return Err("rsa: modulus's top bit clear -- not a 2048-bit modulus");
    }
    if sig.len() != 256 {
        return Err("rsa: signature is not exactly 256 bytes");
    }
    if modulus_be == [0u8; 256] {
        return Err("rsa: zero modulus");
    }
    let n = Bn::from_be(modulus_be)?;
    let s = Bn::from_be(sig)?;
    if s.is_ge(&n) {
        return Err("rsa: signature out of range for the modulus");
    }
    let m = modpow65537(&s, &n)
        .map_err(|_| "rsa: modpow refused input")?;
    // Encode m as 256 big-endian bytes.
    let mut em = [0u8; 256];
    for i in 0..256 {
        let limb = m.limbs[i / 4];
        let sh = (i % 4) * 8;
        em[255 - i] = ((limb >> sh) & 0xff) as u8;
    }
    if em[0] != 0x00 || em[1] != 0x01 {
        return Err("rsa: PKCS#1 v1.5 encoding lacks the 00 01 head");
    }
    let mut i = 2usize;
    while i < 256 && em[i] == 0xff {
        i += 1;
    }
    if i - 2 < 8 {
        return Err("rsa: PKCS#1 v1.5 PS too short (under 8 FF bytes)");
    }
    if i >= 256 || em[i] != 0x00 {
        return Err("rsa: PKCS#1 v1.5 separator 00 missing");
    }
    let tail = &em[i + 1..];
    if tail.len() != 51 {
        return Err("rsa: DigestInfo length wrong for SHA-256");
    }
    if tail[..19] != SHA256_DIGEST_INFO_PREFIX {
        return Err("rsa: DigestInfo prefix is not SHA-256's");
    }
    if tail[19..] != digest[..] {
        return Err("rsa: DigestInfo digest differs from the message digest");
    }
    Ok(())
}
