"""
Nyrqis Presentation Tool — slide presentation application.

Features:
- Create and edit slides with text content
- Multiple slide layouts (title, content, two-column, image, blank)
- Slide transitions (fade, slide, zoom, none)
- Presenter notes
- Slide thumbnails
- Presentation mode with timer
- Keyboard navigation throughout
"""

import time
import hashlib
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Tuple
from datetime import datetime


# ─── Data Classes ────────────────────────────────────────────────────────


class SlideLayout(Enum):
    TITLE = "title"
    TITLE_CONTENT = "title_content"
    TWO_COLUMN = "two_column"
    SECTION_HEADER = "section_header"
    IMAGE = "image"
    QUOTE = "quote"
    BLANK = "blank"


class TransitionType(Enum):
    NONE = "none"
    FADE = "fade"
    SLIDE_LEFT = "slide_left"
    SLIDE_RIGHT = "slide_right"
    SLIDE_UP = "slide_up"
    ZOOM = "zoom"
    DISSOLVE = "dissolve"


LAYOUT_ICONS = {
    SlideLayout.TITLE: "📰",
    SlideLayout.TITLE_CONTENT: "📋",
    SlideLayout.TWO_COLUMN: "▥",
    SlideLayout.SECTION_HEADER: "📑",
    SlideLayout.IMAGE: "🖼️",
    SlideLayout.QUOTE: "💬",
    SlideLayout.BLANK: "📄",
}


@dataclass
class SlideElement:
    """An element on a slide (text, image, shape)."""
    element_type: str = "text"  # text, heading, subheading, bullet, image, shape
    content: str = ""
    x: int = 50
    y: int = 50
    width: int = 900
    height: int = 100
    font_size: int = 24
    font_color: str = "#ffffff"
    bg_color: str = "transparent"
    bold: bool = False
    italic: bool = False
    alignment: str = "left"  # left, center, right


@dataclass
class Slide:
    """A presentation slide."""
    layout: SlideLayout = SlideLayout.TITLE_CONTENT
    title: str = ""
    content: str = ""
    bullets: List[str] = field(default_factory=list)
    notes: str = ""
    # Two-column
    left_content: str = ""
    right_content: str = ""
    # Style
    bg_color: str = "#1a1b26"
    title_color: str = "#7aa2f7"
    text_color: str = "#c0caf5"
    accent_color: str = "#bb9af7"
    # Image
    image_url: str = ""
    image_caption: str = ""
    # Transition
    transition: TransitionType = TransitionType.FADE
    transition_duration_ms: int = 500
    # Elements
    elements: List[SlideElement] = field(default_factory=list)
    # Metadata
    slide_number: int = 0
    hidden: bool = False
    notes_visible: bool = False
    slide_id: str = ""

    def __post_init__(self):
        if not self.slide_id:
            self.slide_id = hashlib.md5(f"slide{self.slide_number}{time.time()}".encode()).hexdigest()[:8]

    @property
    def display_title(self) -> str:
        return f"Slide {self.slide_number}: {self.title}" if self.title else f"Slide {self.slide_number}"

    @property
    def layout_icon(self) -> str:
        return LAYOUT_ICONS.get(self.layout, "📄")

    @property
    def content_preview(self) -> str:
        if self.bullets:
            return self.bullets[0][:50]
        return self.content[:50] if self.content else ""


@dataclass
class Presentation:
    """A slide deck."""
    name: str
    author: str = ""
    description: str = ""
    slides: List[Slide] = field(default_factory=list)
    # Settings
    default_transition: TransitionType = TransitionType.FADE
    slide_size: str = "16:9"  # 16:9, 4:3, custom
    # Metadata
    created: float = field(default_factory=time.time)
    modified: float = field(default_factory=time.time)
    presentation_id: str = ""

    def __post_init__(self):
        if not self.presentation_id:
            self.presentation_id = hashlib.md5(f"{self.name}{self.created}".encode()).hexdigest()[:8]

    @property
    def slide_count(self) -> int:
        return len(self.slides)

    @property
    def visible_slides(self) -> int:
        return sum(1 for s in self.slides if not s.hidden)

    @property
    def total_notes(self) -> int:
        return sum(1 for s in self.slides if s.notes)

    @property
    def time_ago(self) -> str:
        diff = time.time() - self.modified
        if diff < 60:
            return "just now"
        elif diff < 3600:
            return f"{int(diff // 60)}m ago"
        elif diff < 86400:
            return f"{int(diff // 3600)}h ago"
        return datetime.fromtimestamp(self.modified).strftime("%b %d")


# ─── Presentation Tool ───────────────────────────────────────────────────


class PresentationTool:
    """
    Slide presentation tool for Nyrqis OS.
    """

    def __init__(self):
        self._presentations: List[Presentation] = []
        self._current_presentation: Optional[Presentation] = None
        self._current_slide: int = 0
        self._selected_index: int = 0
        self._view_mode: str = "library"  # library, editor, presenter, notes
        # Presenter mode
        self._presenter_active: bool = False
        self._presenter_start: float = 0.0
        self._presenter_elapsed: float = 0.0

        self._init_sample_presentations()

    def _init_sample_presentations(self) -> None:
        # Nyrqis OS Demo Presentation
        slides1 = [
            Slide(SlideLayout.TITLE, "Nyrqis OS", "The Future of Desktop Computing",
                  transition=TransitionType.FADE, bg_color="#1a1b26",
                  notes="Welcome everyone! Today we'll explore Nyrqis OS."),
            Slide(SlideLayout.TITLE_CONTENT, "What is Nyrqis OS?",
                  "A modern, open-source desktop operating system built with:",
                  bullets=["Wayland compositor for smooth rendering",
                           "Rust-based system components for safety",
                           "Apple HIG-inspired design language",
                           "Android-style gesture navigation",
                           "Built-in privacy and security features"],
                  transition=TransitionType.SLIDE_LEFT,
                  notes="Nyrqis OS combines the best of modern OS design."),
            Slide(SlideLayout.TWO_COLUMN, "Key Features",
                  left_content="🎯 Design\n• Clean, minimal UI\n• Adaptive layouts\n• Dark/Light themes\n• Custom accent colors",
                  right_content="⚡ Performance\n• 60fps animations\n• Low latency input\n• Efficient memory use\n• Quick app launch",
                  transition=TransitionType.ZOOM,
                  notes="Highlight both design philosophy and technical performance."),
            Slide(SlideLayout.SECTION_HEADER, "Technical Architecture",
                  "Built on solid foundations",
                  transition=TransitionType.FADE,
                  notes="Let's dive into the technical details."),
            Slide(SlideLayout.TITLE_CONTENT, "System Architecture",
                  "Layered architecture for reliability:",
                  bullets=["Hardware abstraction layer (HAL)",
                           "Rust-based kernel modules",
                           "Wayland compositor (nyrqis-compositor)",
                           "Desktop shell and panels",
                           "Application framework"],
                  transition=TransitionType.SLIDE_LEFT,
                  notes="Each layer has clear responsibilities."),
            Slide(SlideLayout.QUOTE, "Innovation",
                  '"The best way to predict the future is to invent it."',
                  notes="Alan Kay said this, and it drives our philosophy."),
            Slide(SlideLayout.TITLE_CONTENT, "Roadmap",
                  "Upcoming milestones:",
                  bullets=["Q4 2026: Beta release with core apps",
                           "Q1 2027: Package manager and app store",
                           "Q2 2027: Mobile companion app",
                           "Q3 2027: Enterprise features",
                           "2028: Full hardware certification program"],
                  transition=TransitionType.DISSOLVE,
                  notes="Share the vision for where we're headed."),
            Slide(SlideLayout.TITLE, "Thank You", "Questions?",
                  bg_color="#1a1b26",
                  notes="Open the floor for questions. Demo available."),
        ]
        for i, slide in enumerate(slides1):
            slide.slide_number = i + 1
        self._presentations.append(Presentation(
            "Nyrqis OS — Introduction", "Nyrqis Team",
            "Overview of Nyrqis OS features and roadmap",
            slides1, created=time.time() - 86400, modified=time.time() - 3600,
        ))

        # Technical Deep Dive
        slides2 = [
            Slide(SlideLayout.TITLE, "Nyrqis Deep Dive", "Compositor & Rendering Pipeline",
                  notes="Technical deep dive into our rendering system."),
            Slide(SlideLayout.TITLE_CONTENT, "Wayland Compositor",
                  "Our custom Wayland compositor provides:",
                  bullets=["Hardware-accelerated rendering via DRM/KMS",
                           " tear-free presentation with v-sync",
                           " Multi-monitor support with per-display scaling",
                           " Fractional scaling for HiDPI displays",
                           " Color management and HDR support"],
                  notes="The compositor is the heart of the display stack."),
            Slide(SlideLayout.TITLE_CONTENT, "GPU Integration",
                  "Supporting multiple GPU backends:",
                  bullets=["Vulkan for modern GPUs",
                           "OpenGL ES via EGL for compatibility",
                           "GBM buffer allocation",
                           "DMA-BUF sharing between clients",
                           "NVIDIA proprietary driver support"],
                  notes="We support both open and proprietary drivers."),
        ]
        for i, slide in enumerate(slides2):
            slide.slide_number = i + 1
        self._presentations.append(Presentation(
            "Nyrqis Deep Dive", "Nyrqis Team",
            "Technical details of the rendering pipeline",
            slides2, created=time.time() - 172800, modified=time.time() - 86400,
        ))

        # Team Standup
        slides3 = [
            Slide(SlideLayout.TITLE, "Weekly Standup", "Sprint 14 — September 3, 2026",
                  notes="Quick weekly sync."),
            Slide(SlideLayout.TITLE_CONTENT, "Accomplished",
                  "Last week we completed:",
                  bullets=["File manager with full operations",
                           "Clipboard manager with history",
                           "Settings panel with all categories",
                           "2898 tests passing ✅"],
                  notes="Great progress this sprint!"),
            Slide(SlideLayout.TITLE_CONTENT, "This Week",
                  "Sprint 14 goals:",
                  bullets=["Network manager UI polish",
                           "Notification center improvements",
                           "Performance benchmarking suite",
                           "Documentation updates"],
                  notes="Focus areas for this week."),
        ]
        for i, slide in enumerate(slides3):
            slide.slide_number = i + 1
        self._presentations.append(Presentation(
            "Weekly Standup", "Dev Team",
            "Sprint 14 standup notes",
            slides3, created=time.time() - 604800, modified=time.time() - 86400,
        ))

        self._current_presentation = self._presentations[0]
        self._current_slide = 0

    # ── Slide Operations ──────────────────────────────────────────────

    def add_slide(self, layout: SlideLayout = SlideLayout.TITLE_CONTENT) -> Slide:
        if self._current_presentation:
            num = len(self._current_presentation.slides) + 1
            slide = Slide(layout=layout, slide_number=num,
                          transition=self._current_presentation.default_transition)
            self._current_presentation.slides.append(slide)
            self._current_presentation.modified = time.time()
            return slide
        return None

    def delete_slide(self, index: int) -> bool:
        if self._current_presentation and 0 <= index < len(self._current_presentation.slides):
            self._current_presentation.slides.pop(index)
            # Renumber
            for i, s in enumerate(self._current_presentation.slides):
                s.slide_number = i + 1
            self._current_presentation.modified = time.time()
            return True
        return False

    def duplicate_slide(self, index: int) -> Optional[Slide]:
        if self._current_presentation and 0 <= index < len(self._current_presentation.slides):
            import copy
            original = self._current_presentation.slides[index]
            new_slide = copy.deepcopy(original)
            new_slide.slide_number = len(self._current_presentation.slides) + 1
            new_slide.slide_id = hashlib.md5(f"dup{time.time()}".encode()).hexdigest()[:8]
            self._current_presentation.slides.insert(index + 1, new_slide)
            return new_slide
        return None

    def set_slide_title(self, index: int, title: str) -> bool:
        if self._current_presentation and 0 <= index < len(self._current_presentation.slides):
            self._current_presentation.slides[index].title = title
            self._current_presentation.modified = time.time()
            return True
        return False

    def set_slide_content(self, index: int, content: str) -> bool:
        if self._current_presentation and 0 <= index < len(self._current_presentation.slides):
            self._current_presentation.slides[index].content = content
            self._current_presentation.modified = time.time()
            return True
        return False

    def set_slide_notes(self, index: int, notes: str) -> bool:
        if self._current_presentation and 0 <= index < len(self._current_presentation.slides):
            self._current_presentation.slides[index].notes = notes
            return True
        return False

    def set_transition(self, index: int, transition: TransitionType) -> bool:
        if self._current_presentation and 0 <= index < len(self._current_presentation.slides):
            self._current_presentation.slides[index].transition = transition
            return True
        return False

    def toggle_hidden(self, index: int) -> bool:
        if self._current_presentation and 0 <= index < len(self._current_presentation.slides):
            slide = self._current_presentation.slides[index]
            slide.hidden = not slide.hidden
            return slide.hidden
        return False

    # ── Presenter Mode ────────────────────────────────────────────────

    def start_presentation(self) -> bool:
        if self._current_presentation and self._current_presentation.slides:
            self._presenter_active = True
            self._presenter_start = time.time()
            self._current_slide = 0
            self._view_mode = "presenter"
            return True
        return False

    def stop_presentation(self) -> float:
        self._presenter_active = False
        elapsed = time.time() - self._presenter_start
        self._view_mode = "editor"
        return elapsed

    def next_slide(self) -> bool:
        if self._current_presentation:
            if self._current_slide < len(self._current_presentation.slides) - 1:
                self._current_slide += 1
                return True
        return False

    def prev_slide(self) -> bool:
        if self._current_slide > 0:
            self._current_slide -= 1
            return True
        return False

    # ── Navigation ────────────────────────────────────────────────────

    def select_up(self) -> None:
        self._selected_index = max(0, self._selected_index - 1)

    def select_down(self) -> None:
        if self._current_presentation:
            max_idx = len(self._current_presentation.slides) - 1
            self._selected_index = min(max_idx, self._selected_index + 1)

    def get_selected_slide(self) -> Optional[Slide]:
        if self._current_presentation and 0 <= self._selected_index < len(self._current_presentation.slides):
            return self._current_presentation.slides[self._selected_index]
        return None

    def set_view(self, mode: str) -> None:
        self._view_mode = mode
        if mode == "editor":
            self._selected_index = self._current_slide

    def select_presentation(self, index: int) -> bool:
        if 0 <= index < len(self._presentations):
            self._current_presentation = self._presentations[index]
            self._current_slide = 0
            self._selected_index = 0
            return True
        return False

    # ── Properties ────────────────────────────────────────────────────

    @property
    def presentations(self) -> List[Presentation]:
        return list(self._presentations)

    @property
    def current_presentation(self) -> Optional[Presentation]:
        return self._current_presentation

    @property
    def current_slide(self) -> int:
        return self._current_slide

    @property
    def selected_index(self) -> int:
        return self._selected_index

    @property
    def view_mode(self) -> str:
        return self._view_mode

    @property
    def presenter_active(self) -> bool:
        return self._presenter_active

    @property
    def presenter_elapsed(self) -> str:
        if not self._presenter_active:
            return "0:00"
        elapsed = time.time() - self._presenter_start
        m = int(elapsed // 60)
        s = int(elapsed % 60)
        return f"{m}:{s:02d}"

    # ── Rendering ─────────────────────────────────────────────────────

    def render_library(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(" 📊 Presentation Tool")
        lines.append("─" * width)

        for i, pres in enumerate(self._presentations):
            marker = "▸" if i == self._selected_index else " "
            current = " 🟢" if pres == self._current_presentation else ""
            lines.append(f"{marker} {pres.name}{current}")
            lines.append(f"   {pres.author} | {pres.slide_count} slides | {pres.total_notes} notes | Modified: {pres.time_ago}")
            if pres.description:
                lines.append(f"   {pres.description[:width - 5]}")
            lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Open editor  P:Present  Esc:Back")
        return lines

    def render_editor(self, width: int = 70) -> List[str]:
        if not self._current_presentation:
            return ["No presentation selected"]

        lines = []
        pres = self._current_presentation
        lines.append(f" 📝 {pres.name} — Editor ({pres.slide_count} slides)")
        lines.append("─" * width)

        for i, slide in enumerate(pres.slides):
            marker = "▸" if i == self._selected_index else " "
            hidden = " 👁️‍🗨️" if slide.hidden else ""
            notes_icon = " 📝" if slide.notes else ""
            trans = f" [{slide.transition.value}]" if slide.transition != TransitionType.NONE else ""
            lines.append(f"{marker} {slide.layout_icon} Slide {slide.slide_number}: {slide.title or '(untitled)'}{hidden}{notes_icon}{trans}")
            if slide.content_preview:
                lines.append(f"   {slide.content_preview}")
            lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Edit slide  N:New slide  P:Present")
        lines.append(" Del:Delete  D:Duplicate  Esc:Back to library")
        return lines

    def render_slide(self, width: int = 70) -> List[str]:
        """Render a single slide for editing."""
        slide = self.get_selected_slide()
        if not slide:
            return ["No slide selected"]

        lines = []
        lines.append(f" ✏️  Editing Slide {slide.slide_number}")
        lines.append("─" * width)

        # Title
        lines.append(f" Title: {slide.title or '(empty)'}")
        lines.append(f" Layout: {slide.layout.value} | Transition: {slide.transition.value}")
        lines.append("─" * width)

        # Content based on layout
        if slide.layout == SlideLayout.TITLE:
            lines.append("")
            lines.append(f"  ╔{'═' * (width - 4)}╗")
            lines.append(f"  ║{slide.title.center(width - 4)}║")
            lines.append(f"  ║{slide.content.center(width - 4)}║")
            lines.append(f"  ╚{'═' * (width - 4)}╝")
        elif slide.layout == SlideLayout.TITLE_CONTENT:
            lines.append(f"  {slide.title}")
            lines.append("")
            for bullet in slide.bullets:
                lines.append(f"  • {bullet}")
            if not slide.bullets and slide.content:
                lines.append(f"  {slide.content}")
        elif slide.layout == SlideLayout.TWO_COLUMN:
            lines.append(f"  {slide.title}")
            lines.append("")
            left_lines = slide.left_content.split("\n")
            right_lines = slide.right_content.split("\n")
            max_lines = max(len(left_lines), len(right_lines))
            col_w = (width - 6) // 2
            for i in range(max_lines):
                left = left_lines[i] if i < len(left_lines) else ""
                right = right_lines[i] if i < len(right_lines) else ""
                lines.append(f"  {left:<{col_w}} | {right}")
        elif slide.layout == SlideLayout.QUOTE:
            lines.append("")
            lines.append(f"  ╭{'─' * (width - 4)}╮")
            lines.append(f"  │ {slide.content}")
            lines.append(f"  ╰{'─' * (width - 4)}╯")
            lines.append(f"  — {slide.title}")

        # Notes
        if slide.notes:
            lines.append("")
            lines.append(f" 📝 Notes: {slide.notes[:width - 12]}")

        lines.append("─" * width)
        lines.append(" Esc:Back to editor")
        return lines

    def render_presenter(self, width: int = 70) -> List[str]:
        if not self._current_presentation or not self._current_presentation.slides:
            return ["No slides to present"]

        slide = self._current_presentation.slides[self._current_slide]
        lines = []

        # Slide content
        lines.append(f" ┌{'─' * (width - 2)}┐")

        if slide.layout == SlideLayout.TITLE:
            lines.append(f" │{' ' * ((width - 4 - len(slide.title)) // 2)}{slide.title}{' ' * ((width - 4 - len(slide.title) + 1) // 2)}│")
            lines.append(f" │{' ' * ((width - 4 - len(slide.content)) // 2)}{slide.content}{' ' * ((width - 4 - len(slide.content) + 1) // 2)}│")
        elif slide.layout == SlideLayout.TITLE_CONTENT:
            lines.append(f" │  {slide.title:<{width - 5}}│")
            lines.append(f" │{'─' * (width - 3)}│")
            for bullet in slide.bullets:
                lines.append(f" │  • {bullet:<{width - 6}}│")
        elif slide.layout == SlideLayout.TWO_COLUMN:
            lines.append(f" │  {slide.title:<{width - 5}}│")
            lines.append(f" │{'─' * (width - 3)}│")
            left_lines = slide.left_content.split("\n")
            right_lines = slide.right_content.split("\n")
            col_w = (width - 8) // 2
            for i in range(max(len(left_lines), len(right_lines))):
                left = left_lines[i] if i < len(left_lines) else ""
                right = right_lines[i] if i < len(right_lines) else ""
                line = f"  {left:<{col_w}} │ {right:<{col_w}}"
                lines.append(f" │{line:<{width - 3}}│")
        elif slide.layout == SlideLayout.QUOTE:
            lines.append(f" │{' ' * 5}{' ' * ((width - 12 - len(slide.content)) // 2)}{slide.content}│")
            lines.append(f" │{' ' * (width - 8 - len(slide.title))}— {slide.title}│")

        lines.append(f" └{'─' * (width - 2)}┘")

        # Presenter bar
        total = len(self._current_presentation.slides)
        progress = f"[{self._current_slide + 1}/{total}]"
        elapsed = self.presenter_elapsed
        lines.append(f" {progress} Slide {slide.slide_number} | ⏱️ {elapsed}")

        # Notes
        if slide.notes:
            lines.append(f" 📝 {slide.notes[:width - 5]}")

        lines.append("─" * width)
        lines.append(" ←→:Navigate  Space:Next  Esc:Stop  N:Notes toggle")
        return lines

    def render_notes(self, width: int = 70) -> List[str]:
        slide = self.get_selected_slide()
        if not slide:
            return ["No slide selected"]

        lines = []
        lines.append(f" 📝 Presenter Notes — Slide {slide.slide_number}")
        lines.append("─" * width)
        lines.append(f" Title: {slide.title}")
        lines.append("─" * width)

        if slide.notes:
            for line in slide.notes.split("\n"):
                lines.append(f" {line}")
        else:
            lines.append("  (no notes)")

        lines.append("")
        lines.append("─" * width)
        lines.append(" Esc:Back")
        return lines

    def render(self, width: int = 70, height: int = 30) -> List[str]:
        renderers = {
            "editor": self.render_editor,
            "slide": self.render_slide,
            "presenter": self.render_presenter,
            "notes": self.render_notes,
        }
        renderer = renderers.get(self._view_mode, self.render_library)
        return renderer(width)

    # ── Keyboard Handling ─────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[str]:
        if self._view_mode == "presenter":
            return self._handle_presenter_key(key)
        elif self._view_mode == "slide":
            return self._handle_slide_key(key)
        elif self._view_mode == "notes":
            return self._handle_notes_key(key)
        elif self._view_mode == "editor":
            return self._handle_editor_key(key)
        return self._handle_library_key(key)

    def _handle_library_key(self, key: str) -> Optional[str]:
        if key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "Enter":
            self.select_presentation(self._selected_index)
            self.set_view("editor")
            return "editor"
        elif key == "p":
            self.select_presentation(self._selected_index)
            return "present" if self.start_presentation() else "present_failed"
        return None

    def _handle_editor_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("library")
            return "back"
        elif key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "n":
            self.add_slide()
            self._selected_index = len(self._current_presentation.slides) - 1
            return "new_slide"
        elif key == "Delete":
            return "delete_slide" if self.delete_slide(self._selected_index) else "delete_failed"
        elif key == "d":
            return "duplicate" if self.duplicate_slide(self._selected_index) else "duplicate_failed"
        elif key == "p":
            self._current_slide = self._selected_index
            return "present" if self.start_presentation() else "present_failed"
        return None

    def _handle_presenter_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.stop_presentation()
            return "stop_presentation"
        elif key == "ArrowRight" or key == " ":
            return "next" if self.next_slide() else "end_of_slides"
        elif key == "ArrowLeft":
            return "prev" if self.prev_slide() else "start_of_slides"
        return None

    def _handle_slide_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("editor")
            return "back"
        return None

    def _handle_notes_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("editor")
            return "back"
        return None
