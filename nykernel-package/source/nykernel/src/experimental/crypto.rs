//! Minimal checksum/hash primitives for format validation.
//!
//! Added to close a named gap in `experimental::dex`: the DEX container
//! format records an Adler-32 `checksum` and a SHA-1 `signature` over
//! its own bytes, and the parser used to parse-but-not-verify them
//! ("no Adler-32/SHA-1 implementation in this kernel yet"). These are
//! independent, well-specified algorithms with published test vectors,
//! so correctness is provable with a known-answer test rather than
//! only "the real file happened to validate" -- see `dex_demo`.
//!
//! Scope note (honest): SHA-1 is implemented here as a *format
//! integrity* check (matching what DEX/APK use), not as a modern
//! security primitive -- SHA-1 is cryptographically broken for
//! collision resistance and this kernel makes no claim otherwise. It
//! is also not constant-time; nothing here protects a secret.
//!
//! Both functions are `no_std` (only `alloc`, for the SHA-1 padding
//! buffer) and are otherwise pure.

use alloc::vec::Vec;

/// Adler-32 (RFC 1950 §9), the checksum the DEX header carries and
/// `zlib.adler32` computes. `MOD` is the largest prime below 65536.
pub fn adler32(data: &[u8]) -> u32 {
    const MOD: u32 = 65521;
    let mut a: u32 = 1;
    let mut b: u32 = 0;
    for &byte in data {
        a = (a + byte as u32) % MOD;
        b = (b + a) % MOD;
    }
    (b << 16) | a
}

/// SHA-1 (FIPS 180-4). Returns the 20-byte digest.
///
/// Straightforward implementation: pad to a 64-byte block boundary
/// with the mandatory `0x80` byte, zero bytes, and the 64-bit
/// big-endian message length, then run the 80-round compression
/// function over each block.
pub fn sha1(data: &[u8]) -> [u8; 20] {
    let mut h: [u32; 5] = [
        0x6745_2301,
        0xEFCD_AB89,
        0x98BA_DCFE,
        0x1032_5476,
        0xC3D2_E1F0,
    ];

    // Padding: message || 0x80 || 0x00... || 64-bit BE bit length.
    let message_bit_len = (data.len() as u64).wrapping_mul(8);
    let mut padded: Vec<u8> = Vec::with_capacity(data.len() + 72);
    padded.extend_from_slice(data);
    padded.push(0x80);
    while padded.len() % 64 != 56 {
        padded.push(0);
    }
    padded.extend_from_slice(&message_bit_len.to_be_bytes());

    for block in padded.chunks_exact(64) {
        let mut w = [0u32; 80];
        for (i, word) in w.iter_mut().enumerate().take(16) {
            *word = u32::from_be_bytes([
                block[4 * i],
                block[4 * i + 1],
                block[4 * i + 2],
                block[4 * i + 3],
            ]);
        }
        for i in 16..80 {
            w[i] = (w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16]).rotate_left(1);
        }

        let (mut a, mut b, mut c, mut d, mut e) = (h[0], h[1], h[2], h[3], h[4]);
        for (i, &wi) in w.iter().enumerate() {
            let (f, k) = match i {
                0..=19 => ((b & c) | ((!b) & d), 0x5A82_7999u32),
                20..=39 => (b ^ c ^ d, 0x6ED9_EBA1),
                40..=59 => ((b & c) | (b & d) | (c & d), 0x8F1B_BCDC),
                _ => (b ^ c ^ d, 0xCA62_C1D6),
            };
            let temp = a
                .rotate_left(5)
                .wrapping_add(f)
                .wrapping_add(e)
                .wrapping_add(k)
                .wrapping_add(wi);
            e = d;
            d = c;
            c = b.rotate_left(30);
            b = a;
            a = temp;
        }

        h[0] = h[0].wrapping_add(a);
        h[1] = h[1].wrapping_add(b);
        h[2] = h[2].wrapping_add(c);
        h[3] = h[3].wrapping_add(d);
        h[4] = h[4].wrapping_add(e);
    }

    let mut out = [0u8; 20];
    for (i, word) in h.iter().enumerate() {
        out[4 * i..4 * i + 4].copy_from_slice(&word.to_be_bytes());
    }
    out
}
