---
title: Nyrqis Design Language
document_id: DESIGN-LANGUAGE
version: 1.0.0
status: Draft
owners: [Nyrqis UI]
created: 2026-09-11
ai_assisted: true
depends_on: [NFS-001, NFS-006, ADR-0025]
---

# Nyrqis Design Language — HIG structure, Material feel

The design language for Nyrqis surfaces (shell, apps, installer) borrows
**Apple's Human Interface Guidelines** for structure — clarity, deference,
depth — and **Google's Material Design** for feel — bold but physical
motion, generous touch targets, and a stateful elevation model. One
sentence: *the calm organization of macOS with the responsive physicality
of Android.*

This document is normative for new UI work and the target for retrofitting
existing surfaces. The reference implementation is the default shell
(`shell/defaults/default-shell.nstudio`), restyled to this spec in the
same change that introduced it.

## 1. Three principles, mapped

| Principle | Origin | What it means in Nyrqis |
|---|---|---|
| **Clarity** | HIG | Text is legible at every size; icons are precise; decoration never competes with content. System text renders in Roboto Flex (the shipped font stack) with SF-style optical sizing when available. |
| **Deference** | HIG | Chrome recedes: translucent surfaces over wallpaper, content-first layouts, controls appear when relevant. The desktop is the content; the taskbar/dock is a thin lens over it. |
| **Physical response** | Material | Every interaction acknowledges: a press ripples/scales, a menu springs in, a dismissal follows the finger. Nothing snaps instantaneously, nothing lags past 300 ms. |

## 2. Design tokens

Tokens are the single vocabulary; components reference tokens, never raw
values. The default shell carries a `designTokens` section that is the
machine-readable form of this table (the `.nstudio` loader tolerates the
extra section; renderers that ignore it lose polish, not correctness).

### 2.1 Spacing (4-pt baseline grid)

| Token | Value | Use |
|---|---|---|
| `space.xs` | 4 px | Icon-to-label gaps, badge insets |
| `space.sm` | 8 px | Control padding inside buttons/menus |
| `space.md` | 12 px | List-item padding, card insets |
| `space.lg` | 16 px | Panel margins, section separation |
| `space.xl` | 24 px | Screen-edge margins for floating panels |

HIG alignment: content margins stay consistent across screens; a menu's
edges align to the 4-pt grid even when its width is content-driven.

### 2.2 Corner radius (Material continuity)

| Token | Value | Use |
|---|---|---|
| `radius.sm` | 8 px | Buttons, inputs, taskbar items |
| `radius.md` | 12 px | Cards, menus, popovers |
| `radius.lg` | 16 px | App windows, the start menu |
| `radius.full` | height/2 | Pills, avatars, dock background |

### 2.3 Elevation & translucency (HIG depth, Material z-model)

Surfaces stack by elevation; translucency (HIG *vibrancy*) applies from
elevation 2 up so the wallpaper tints panels without hurting contrast:

| Token | Value | Surface |
|---|---|---|
| `surface.base` | opaque | Wallpaper, desktop |
| `surface.bar` | 85% opacity + blur | Taskbar/dock, title bars |
| `surface.raised` | 92% opacity + blur | Menus, popovers, the start menu |
| `surface.dialog` | opaque + 24 px shadow | Modal dialogs, notifications |

Shadow recipe (Material): elevation 2 → `0 1px 2px rgba(0,0,0,.30)`,
elevation 4 → `0 2px 6px rgba(0,0,0,.28)`, elevation 8 →
`0 8px 24px rgba(0,0,0,.35)`. Components above elevation 2 always carry
a 1 px inner hairline (`rgba(255,255,255,.08)`) to separate translucent
chrome from light wallpaper — the HIG vibrancy guardrail.

### 2.4 Typography

| Token | Value |
|---|---|
| `type.family` | Roboto Flex, SF Pro, system-ui, sans-serif |
| `type.title` | 22 px / weight 500 / -0.25 px tracking |
| `type.body` | 15 px / weight 400 |
| `type.label` | 13 px / weight 500 / +0.1 px tracking (Material caption style) |
| `type.mono` | Roboto Mono, SF Mono, monospace |

### 2.5 Color & accent

Follow the platform's existing Eclipse/Solar themes for hue; this spec
constrains **roles**, not hues:

- One accent per mode. The accent colors **interactive** elements only —
  never decoration (Material's "accent as action" rule).
- Text meets WCAG AA on every surface it can appear over; over
  translucent chrome, text renders on the surface's opaque fallback if
  contrast drops below 4.5:1.
- Dark mode is the default (Eclipse); light (Solar) is a full peer —
  every token has both values, no tint-only inverted variants.

## 3. Motion

Physical, quick, and interruptible. Durations and easings below are
expressible in the NUI animation schema (`ui/nstudio.py` accepts
`linear | ease-in | ease-out | ease-in-out | steps`).

| Token | Duration | Easing | Use |
|---|---|---|---|
| `motion.micro` | 100 ms | `ease-out` | Press feedback, toggle snaps |
| `motion.enter` | 200 ms | `ease-out` | Menus, popovers, start menu |
| `motion.exit` | 120 ms | `ease-in` | Dismissals (faster than entries) |
| `motion.move` | 250 ms | `ease-in-out` | Panel repositioning, window restore |
| `motion.emphasis` | 300 ms | `ease-in-out` | Full-screen transitions, first-run reveals |

Rules (each traceable to a source guideline):

1. **Enter fast, exit faster** (HIG): exits take ~60% of their entrance
   duration; the desktop never waits on a dismissal.
2. **Nothing instant, nothing slow** (Material): the shortest perceptible
   transition is 100 ms; nothing user-waited-on exceeds 300 ms.
3. **One arc per surface** (Material *motion pattern*): a menu opens from
   its anchor (taskbar button, dock icon), scaling from the point of
   origin — it does not fade in from nowhere. In `.nstudio` terms, the
   enter animation pairs an `opacity` track with a `scale`/`y` track on
   the same duration/easing.
4. **Interruption is non-destructive** (Material): starting the reverse
   transition mid-flight retargets from the current value; animations are
   state-driven (`bindings`), not fire-and-forget.

## 4. Touch & pointer interaction

| Token | Value |
|---|---|
| `target.min` | 44 × 44 px (HIG minimum; Material 48 dp where hardware is touch-first) |
| `target.gap` | ≥ 8 px between adjacent targets |
| `state.pressed` | scale 0.97 + ripple confined to `radius.sm` |
| `state.hover` | +1 elevation, no color fill (pointer platforms only) |

Desktop-first, but every control must be operable at 44 px because the
same components run on touch panels and convertible hardware.

## 5. Navigation model (the "Android feel" boundary)

The shell keeps HIG structure — a persistent menu bar/taskbar, a single
system-wide search, windows in a stack — and adopts exactly three Android
navigation conventions, the ones that age best on large screens:

1. **System back is universal.** Esc, `Alt+Left`, and (on touch) the
   back gesture all drive one back stack; panels that slide in slide
   back out on back.
2. **Bottom-weighted chrome.** Dock/taskbar sits at the bottom (Material's
   thumb-reachable zone); floating panels anchor to it. Top-of-screen
   chrome is limited to the menu bar.
3. **FAB-equivalent action.** Every primary surface exposes one clearly
   accent-colored primary action (start menu "New", file manager
   "Create"), visually distinct from secondary actions.

Explicitly out of scope (kept HIG/desktop, not mobile): no drawer-style
global navigation on desktop windows, no bottom tab bars inside apps,
no pull-to-refresh outside genuinely refreshable data surfaces.

## 6. Reference implementation mapping

`shell/defaults/default-shell.nstudio` implements this spec as follows:

| Spec item | Shell implementation |
|---|---|
| `designTokens` (§2) | Document-level `designTokens` object (spacing/radius/type/motion) |
| `surface.bar` (§2.3) | Taskbar `translucency: 0.85` + `blur: true` |
| `motion.enter` (§3) | `start_menu_fade` 200 ms `ease-out` opacity track |
| `motion.arc` (§3.3) | `start_menu_rise` 200 ms `ease-out` y-offset track (paired with the fade) |
| `motion.exit` (§3) | `start_menu_drop` 120 ms `ease-in` paired tracks |
| `target.min` (§4) | Taskbar controls sized to 44 px hit height (40 px visual + padding) |
| `type.*` (§2.4) | Clock/labels use `label`; window titles use `title` |
| Accent-as-action (§2.5) | Start button carries the accent; clock/labels stay neutral |

Where a renderer does not yet support a property (blur, scale tracks),
the design degrades to the nearest supported effect (opacity, offset) —
the tokens record intent; the renderer's honesty notes record capability.

## 7. Adoption checklist (for any surface)

- [ ] Spacing snaps to the 4-pt grid; `space.*` tokens, no ad-hoc gaps
- [ ] Corner radii use `radius.*`; nothing sharper than 8 px, nothing rounder than `radius.full`
- [ ] Every interactive element ≥ 44 × 44 px with ≥ 8 px separation
- [ ] Translucent surfaces carry the hairline guardrail and an AA-contrast fallback
- [ ] Accent appears on interactive elements only
- [ ] Transitions use `motion.*` tokens; exits are faster than entrances; menus open from their anchor
- [ ] Exposed to both Eclipse and Solar without special-casing
- [ ] All properties used exist in the component's NUI contract (`ui/contracts/nui-api-v1.json`)

## References

- Apple Human Interface Guidelines — *Designing for macOS* (clarity,
  deference, depth), *Materials* (vibrancy), *Motion* (enter/exit timing)
- Material Design 3 — *Elevation*, *Motion* (duration/easing tokens,
  container transform), *State layers*, *Touch targets*
- NFS-001 (NUI schema), NFS-006 (component vocabulary), ADR-0025
  (NUI runtime consumption)
