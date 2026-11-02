//! Linear framebuffer driver -- the display *output* path.
//!
//! Ported from release 1-71's `src/experimental/framebuffer.rs` (same
//! project, same `x86_64` mapping conventions) and kept under
//! `devices::` because it is now a driver the autoloader binds, not an
//! experiment the demo chain calls. The change from the release version
//! is ownership: `init` is called once by `devices::autoload` and the
//! resulting handle is owned by whoever was granted it, so the mode
//! renderer and any later shell share one mapping instead of each
//! re-mapping the same physical range (`MapToError::PageAlreadyMapped`
//! on the second attempt was the release's failure mode for that).
//!
//! Scope, unchanged and stated plainly: direct-RGB 32bpp only; a
//! palette or EGA-text mode is reported and refused, never drawn into
//! blind. This is not a GPU command-submission path (no command
//! buffers, no validation fast path, no preemption) -- those remain the
//! open Phase 7 work, and FIND-KERNEL-002 (GPU memory clearing between
//! containers) is untouched by a framebuffer driver.

use x86_64::structures::paging::mapper::MapToError;
use x86_64::structures::paging::{
    Mapper, OffsetPageTable, Page, PageTableFlags, PhysFrame, Size4KiB,
};
use x86_64::{PhysAddr, VirtAddr};

use crate::memory::frame_allocator::BumpFrameAllocator;
use crate::multiboot::FramebufferInfo;

#[derive(Debug)]
pub enum FbError {
    UnsupportedFormat,
    BadGeometry,
    /// Inside the kernel's static identity map: mapping 4K pages there
    /// would fight the huge-page entries boot.s installed.
    AddressTooLow,
    MapFailed,
}

pub struct Framebuffer {
    base: *mut u32,
    pub width: u32,
    pub height: u32,
    stride_px: usize,
    /// The loader's scan-out order: true when memory holds R,G,B,X
    /// (UEFI GOP's RGBX mode), false for Multiboot2's B,G,R,X. Both
    /// boot paths land here, so the one bit travels in the vendor tag
    /// rather than each path growing its own renderer.
    rgb_first: bool,
}

/// Maps the framebuffer at virtual == physical and returns a drawing
/// handle.
///
/// # Safety
/// `info` must describe a real framebuffer region (it comes from the
/// loader); the mapper/allocator must be the live kernel ones.
pub unsafe fn init(
    info: FramebufferInfo,
    mapper: &mut OffsetPageTable<'static>,
    fa: &mut BumpFrameAllocator,
) -> Result<Framebuffer, FbError> {
    if info.kind != 1 || info.bpp != 32 {
        return Err(FbError::UnsupportedFormat);
    }
    if info.width == 0
        || info.height == 0
        || info.pitch < info.width * 4
        || info.addr & 0xfff != 0
    {
        return Err(FbError::BadGeometry);
    }
    // boot.s identity-maps the first 1GiB with 2MiB pages; mapping 4K
    // pages inside that range would conflict. Real framebuffers sit
    // far above it (QEMU's std VGA BAR is at 0xfd000000).
    if info.addr < 0x4000_0000 {
        return Err(FbError::AddressTooLow);
    }
    let bytes = info.pitch as u64 * info.height as u64;
    let pages = (bytes + 4095) / 4096;
    let flags = PageTableFlags::PRESENT
        | PageTableFlags::WRITABLE
        | PageTableFlags::NO_EXECUTE
        | PageTableFlags::NO_CACHE
        | PageTableFlags::WRITE_THROUGH;
    for i in 0..pages {
        let phys = PhysFrame::<Size4KiB>::containing_address(PhysAddr::new(info.addr + i * 4096));
        let page = Page::<Size4KiB>::containing_address(VirtAddr::new(info.addr + i * 4096));
        match mapper.map_to(page, phys, flags, fa) {
            Ok(f) => f.flush(),
            Err(MapToError::PageAlreadyMapped(_)) => return Err(FbError::MapFailed),
            Err(_) => return Err(FbError::MapFailed),
        }
    }
    Ok(Framebuffer {
        base: info.addr as *mut u32,
        width: info.width,
        height: info.height,
        stride_px: (info.pitch / 4) as usize,
        rgb_first: info.rgb_first,
    })
}

impl Framebuffer {
    /// The loader's scan-out order, for the boot transcript: a display
    /// that comes up in the wrong channel order is otherwise reported
    /// as "working".
    pub fn rgb_first(&self) -> bool {
        self.rgb_first
    }

    /// The raw mapping, for a renderer that writes 0x00RRGGBB pixels
    /// straight into the buffer (release 1-71's `shell::Fb` is exactly
    /// that contract: a base pointer and a pitch in pixels). The caller
    /// MUST call `convert_scanout_region` over whatever it wrote, or the
    /// pixels keep this renderer's channel order rather than the
    /// display's -- that call is the one place the order is applied.
    pub fn surface(&mut self) -> (*mut u32, usize) {
        (self.base, self.stride_px)
    }

    /// Applies the loader's channel order to a region a raw renderer
    /// wrote into, and to nothing else. R/B swap is its own inverse, so
    /// this is idempotent: converting the same region twice is a no-op
    /// in effect, and a region converted zero times on a BGRX display
    /// needs no conversion at all.
    pub fn convert_scanout_region(&mut self, x: u32, y: u32, w: u32, h: u32) {
        if !self.rgb_first {
            return;
        }
        let x_end = x.saturating_add(w).min(self.width);
        let y_end = y.saturating_add(h).min(self.height);
        for yy in y..y_end {
            let row = yy as usize * self.stride_px;
            for xx in x..x_end {
                unsafe {
                    let p = self.base.add(row + xx as usize);
                    let v = core::ptr::read_volatile(p);
                    core::ptr::write_volatile(p, self.encode(v));
                }
            }
        }
    }

    /// Swaps R and B for an RGBX scan-out; a no-op on the BGRX path.
    /// Callers always speak 0x00RRGGBB, so there is exactly one place
    /// the loader's channel order is applied.
    #[inline]
    fn encode(&self, argb: u32) -> u32 {
        if self.rgb_first {
            (argb & 0xff00_ff00) | ((argb & 0xff) << 16) | ((argb >> 16) & 0xff)
        } else {
            argb
        }
    }

    #[inline]
    pub fn put(&mut self, x: u32, y: u32, argb: u32) {
        if x < self.width && y < self.height {
            let px = self.encode(argb);
            unsafe {
                core::ptr::write_volatile(
                    self.base.add(y as usize * self.stride_px + x as usize),
                    px,
                )
            };
        }
    }

    /// Reads a pixel back in the renderer's own 0x00RRGGBB space, so a
    /// read-back comparison means the same thing on both scan-out
    /// orders instead of silently passing on one and failing on the
    /// other.
    pub fn get(&self, x: u32, y: u32) -> Option<u32> {
        if x < self.width && y < self.height {
            let raw = unsafe {
                core::ptr::read_volatile(self.base.add(y as usize * self.stride_px + x as usize))
            };
            Some(self.encode(raw))
        } else {
            None
        }
    }

    pub fn fill_rect(&mut self, x: u32, y: u32, w: u32, h: u32, argb: u32) {
        let x_end = x.saturating_add(w).min(self.width);
        let y_end = y.saturating_add(h).min(self.height);
        let px = self.encode(argb);
        for yy in y..y_end {
            let row = yy as usize * self.stride_px;
            for xx in x..x_end {
                unsafe { core::ptr::write_volatile(self.base.add(row + xx as usize), px) };
            }
        }
    }

    /// A 1px outline, drawn as four rects so it never loops per-pixel
    /// twice over the same cells.
    pub fn stroke_rect(&mut self, x: u32, y: u32, w: u32, h: u32, argb: u32) {
        self.fill_rect(x, y, w, 1, argb);
        self.fill_rect(x, y.saturating_add(h).saturating_sub(1), w, 1, argb);
        self.fill_rect(x, y, 1, h, argb);
        self.fill_rect(x.saturating_add(w).saturating_sub(1), y, 1, h, argb);
    }

    /// Fills a centred circle by scanline -- the FAB / gesture pill /
    /// touch-target shape. Radius is in pixels; a zero radius draws
    /// nothing rather than dividing by zero. `f64::sqrt` is std-only, so
    /// the span comes from an integer square root (Newton, on u64).
    pub fn fill_circle(&mut self, cx: u32, cy: u32, r: u32, argb: u32) {
        if r == 0 {
            return;
        }
        let r2 = (r * r) as i64;
        let ri = r as i64;
        let px = self.encode(argb);
        let mut dy = -ri;
        while dy <= ri {
            let span = isqrt((r2 - dy * dy) as u64) as i64;
            let y = cy as i64 + dy;
            if y >= 0 && (y as u32) < self.height {
                let x0 = (cx as i64 - span).max(0) as u32;
                let x1 = (cx as i64 + span).min(self.width as i64 - 1) as u32;
                if x0 <= x1 {
                    let row = y as usize * self.stride_px;
                    for x in x0..=x1 {
                        unsafe { core::ptr::write_volatile(self.base.add(row + x as usize), px) };
                    }
                }
            }
            dy += 1;
        }
    }
}

/// Integer square root (Newton's method), for circle spans without
/// pulling a float library into the kernel.
fn isqrt(n: u64) -> u64 {
    if n < 2 {
        return n;
    }
    let mut x = n;
    let mut y = (x + 1) / 2;
    while y < x {
        x = y;
        y = (x + n / x) / 2;
    }
    x
}

/// Reads the framebuffer back and reports whether a known pattern
/// survived the mapping, exactly as the release's self-test did. This
/// is what makes "the display is up" a measured fact rather than a
/// claim: a wrong mapping either faults or reads back other memory.
pub fn read_back_ok(fb: &Framebuffer, probe: &[(u32, u32, u32)]) -> bool {
    probe.iter().all(|&(x, y, want)| fb.get(x, y) == Some(want))
}
