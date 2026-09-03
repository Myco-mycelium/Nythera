from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time
import math
import hashlib


class ArtStyle(Enum):
    BLOCK = "block"
    SMOOTH = "smooth"
    BOLD = "bold"
    SHADOW = "shadow"
    ROMAN = "roman"
    SCRIPT = "script"
    STANDOUT = "standout"
    TWIST = "twist"


class PatternType(Enum):
    GRADIENT = "gradient"
    WAVE = "wave"
    SPIRAL = "spiral"
    CHECKERBOARD = "checkerboard"
    DIAMOND = "diamond"
    HEART = "heart"
    STAR = "star"
    MAZE = "maze"
    FRACTAL = "fractal"
    CIRCLE = "circle"


class Charset(Enum):
    STANDARD = "standard"
    BINARY = "binary"
    HEX = "hex"
    BRAILLE = "braille"
    BLOCK = "block"
    DOTS = "dots"
    CUSTOM = "custom"


@dataclass
class ArtPiece:
    name: str
    text: str
    style: ArtStyle
    width: int
    height: int
    chars: str
    timestamp: float
    is_favorite: bool = False
    tags: list = field(default_factory=list)
    content: str = ""

    @property
    def preview(self) -> str:
        lines = self.content.split("\n") if self.content else []
        return lines[0][:40] if lines else ""

    @property
    def line_count(self) -> int:
        return len(self.content.split("\n")) if self.content else 0


class AsciiArt:
    def __init__(self):
        self._pieces: list[ArtPiece] = []
        self._selected_piece: int = 0
        self._current_text: str = "Nyrqis"
        self._current_style: ArtStyle = ArtStyle.BLOCK
        self._current_width: int = 60
        self._current_height: int = 15
        self._current_charset: Charset = Charset.STANDARD
        self._current_pattern: PatternType = PatternType.GRADIENT
        self._pattern_colors: tuple = ("@", "#", "%", "*", "+", "=", "-", ".", " ")
        self._font_scale: float = 1.0
        self._border: bool = False
        self._invert: bool = False
        self._mirror: bool = False
        self._rotation: int = 0
        self._view: str = "editor"
        self._create_samples()

    def _create_samples(self):
        now = time.time()
        samples = [
            ArtPiece("Nyrqis Logo", "NYRQIS", ArtStyle.BLOCK, 60, 12, "@#%*+=-. ", now - 86400, True, ["logo", "brand"],
                     "  ██╗   ██╗███████╗██╗  ██╗██╗███████╗███████╗\n  ╚██╗ ██╔╝██╔════╝╚██╗██╔╝██║██╔════╝██╔════╝\n   ╚████╔╝ █████╗   ╚███╔╝ ██║█████╗  ███████╗\n    ╚██╔╝  ██╔══╝   ██╔██╗ ██║██╔══╝  ╚════██║\n     ██║   ███████╗██╔╝ ██╗██║███████╗███████║\n     ╚═╝   ╚══════╝╚═╝  ╚═╝╚═╝╚══════╝╚══════╝"),
            ArtPiece("Welcome Wave", "HELLO", ArtStyle.SHADOW, 50, 10, "@#%* ", now - 3600, False, ["greeting"],
                     "    _   _      _ _        __        __         _     _ \n   | | | | ___| | | ___   \\ \\      / /__  _ __| | __| |\n   | |_| |/ _ \\ | |/ _ \\   \\ \\ /\\ / / _ \\| '__| |/ _` |\n   |  _  |  __/ | | (_) |   \\ V  V / (_) | |  | | (_| |\n   |_| |_|\\___|_|_|\\___/     \\_/\\_/ \\___/|_|  |_|\\__,_|"),
            ArtPiece("Diamond Pattern", "", PatternType.DIAMOND, 30, 15, "@#%. ", now - 7200, True, ["pattern", "geometric"],
                     "              .\n             @#\n            @#%%\n           @#%%*\n          @#%%*+\n         @#%%*+=\n        @#%%*+=-\n         @#%%*+=\n          @#%%*\n           @%%\n            @"),
            ArtPiece("Heart", "", PatternType.HEART, 30, 20, "@#%", now - 1800, False, ["love", "heart"],
                     "    @@@@@@     @@@@@@\n  @@######@@ @@######@@\n @@########@@@@########@@\n @@######################@@\n @@######################@@\n @@######################@@\n  @@####################@@\n   @@##################@@\n    @@################@@\n      @@############@@\n        @@########@@\n          @@####@@\n            @@@@\n              @@"),
        ]
        self._pieces = samples

    @property
    def selected_piece(self) -> Optional[ArtPiece]:
        if 0 <= self._selected_piece < len(self._pieces):
            return self._pieces[self._selected_piece]
        return None

    @property
    def total_pieces(self) -> int:
        return len(self._pieces)

    @property
    def favorites_count(self) -> int:
        return sum(1 for p in self._pieces if p.is_favorite)

    def select_piece(self, idx: int):
        if 0 <= idx < len(self._pieces):
            self._selected_piece = idx

    def generate_text_art(self, text: str) -> str:
        # Simple block letter generation
        letters = {
            'N': ["█▄▀", "█▀█", "█ █"],
            'Y': ["█▀█", " █ ", " █ "],
            'R': ["█▀▄", "█▀▀", "█ █"],
            'Q': ["█▀█", "█ █", "▀▀█"],
            'I': ["▀█▀", " █ ", " █ "],
            'S': ["█▀▀", "▀▀█", "▀▀▀"],
            'H': ["█ █", "█▀█", "█ █"],
            'E': ["█▀▀", "█▀ ", "▀▀▀"],
            'L': ["█  ", "█  ", "▀▀▀"],
            'O': ["█▀█", "█ █", "▀▀▀"],
            'W': ["█ █", "█ █", "▀▄▀"],
            'D': ["█▀▄", "█ █", "▀▀ "],
            'A': ["▀█▀", "█▀█", "█ █"],
            'B': ["█▀▄", "█▀▄", "▀▀▀"],
            'C': ["█▀▀", "█  ", "▀▀▀"],
            'F': ["█▀▀", "█▀ ", "█  "],
            'G': ["█▀▀", "█ █", "▀▀█"],
            'J': [" ▄█", " █ ", "▀▀ "],
            'K': ["█ █", "█▀▄", "█ █"],
            'M': ["█▀█", "█▀█", "█ █"],
            'P': ["█▀▄", "█▀ ", "█  "],
            'T': ["▀█▀", " █ ", " █ "],
            'U': ["█ █", "█ █", "▀▀▀"],
            'V': ["█ █", "█ █", "▀▄▀"],
            'X': ["█ █", "▀▄▀", "█ █"],
            'Z': ["▀▀█", " █ ", "█▀▀"],
            ' ': ["   ", "   ", "   "],
        }
        lines = ["", "", ""]
        for char in text.upper():
            glyph = letters.get(char, ["???", "???", "???"])
            for i in range(3):
                lines[i] += glyph[i] + " "
        return "\n".join(lines)

    def generate_pattern(self, pattern: PatternType, width: int = 40, height: int = 20) -> str:
        chars = self._pattern_colors
        lines = []
        if pattern == PatternType.GRADIENT:
            for y in range(height):
                line = ""
                for x in range(width):
                    val = (x + y) % len(chars)
                    line += chars[val]
                lines.append(line)
        elif pattern == PatternType.WAVE:
            for y in range(height):
                line = ""
                for x in range(width):
                    wave = int(math.sin(x * 0.3 + y * 0.5) * 3 + 3)
                    val = wave % len(chars)
                    line += chars[val]
                lines.append(line)
        elif pattern == PatternType.CHECKERBOARD:
            for y in range(height):
                line = ""
                for x in range(width):
                    val = chars[0] if (x // 3 + y // 2) % 2 == 0 else chars[-1]
                    line += val
                lines.append(line)
        elif pattern == PatternType.DIAMOND:
            cx, cy = width // 2, height // 2
            for y in range(height):
                line = ""
                for x in range(width):
                    dist = abs(x - cx) + abs(y - cy)
                    val = min(dist, len(chars) - 1)
                    line += chars[val]
                lines.append(line)
        elif pattern == PatternType.CIRCLE:
            cx, cy = width // 2, height // 2
            radius = min(cx, cy) - 1
            for y in range(height):
                line = ""
                for x in range(width):
                    dist = math.sqrt((x - cx) ** 2 + ((y - cy) * 1.5) ** 2)
                    diff = abs(dist - radius)
                    if diff < 1:
                        line += chars[0]
                    elif diff < 2:
                        line += chars[1]
                    elif diff < 3:
                        line += chars[2]
                    else:
                        line += " "
                lines.append(line)
        elif pattern == PatternType.HEART:
            for y in range(height):
                line = ""
                for x in range(width):
                    nx = (x / width - 0.5) * 2
                    ny = (y / height - 0.5) * 2
                    val = (nx**2 + ny**2 - 1)**3 - nx**2 * ny**3
                    if val < 0:
                        line += chars[0]
                    else:
                        line += " "
                lines.append(line)
        else:
            for y in range(height):
                line = ""
                for x in range(width):
                    h = hashlib.md5(f"{x}{y}".encode()).hexdigest()
                    val = int(h[:1], 16) % len(chars)
                    line += chars[val]
                lines.append(line)
        return "\n".join(lines)

    def toggle_favorite(self):
        p = self.selected_piece
        if p:
            p.is_favorite = not p.is_favorite

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                     NYRQIS ASCII ART GENERATOR                             ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        lines.append(f"  Text: {self._current_text}  Style: {self._current_style.value}  Width: {self._current_width}  Height: {self._current_height}")
        lines.append(f"  Charset: {self._current_charset.value}  Border: {'ON' if self._border else 'OFF'}  Invert: {'ON' if self._invert else 'OFF'}")
        lines.append("")
        lines.append(f"  Pieces: {self.total_pieces}  ⭐ {self.favorites_count}")
        lines.append("")
        for i, p in enumerate(self._pieces):
            sel = "▶" if i == self._selected_piece else " "
            fav = "⭐" if p.is_favorite else " "
            lines.append(f"  {sel}{fav} {p.name}  {p.style.value if hasattr(p.style, 'value') else 'pattern'}  {p.width}×{p.line_count}  {', '.join(p.tags)}")
        lines.append("")
        lines.append("  ── Generated Art ──")
        art = self.generate_text_art(self._current_text)
        for line in art.split("\n"):
            lines.append(f"  {line}")
        lines.append("")
        lines.append("  ── Patterns ──")
        for p in PatternType:
            sel = "▶" if p == self._current_pattern else " "
            lines.append(f"  {sel} {p.value}")
        lines.append("")
        lines.append("  [T]ext  [S]tyle  [W]idth  [H]eight  [P]attern  [G]enerate  [F]avorite  [C]harset")
        return lines

    def render_preview(self) -> list:
        p = self.selected_piece
        if not p:
            return ["  No piece selected"]
        lines = []
        lines.append(f"  ── {p.name} ──")
        lines.append(f"  Style: {p.style.value if hasattr(p.style, 'value') else 'pattern'}  Size: {p.width}×{p.line_count}")
        lines.append(f"  Tags: {', '.join(p.tags) if p.tags else 'none'}")
        lines.append("")
        for line in p.content.split("\n"):
            lines.append(f"  {line}")
        return lines

    def render_pattern(self) -> list:
        lines = []
        lines.append(f"  ── Pattern: {self._current_pattern.value} ({self._current_width}×{self._current_height}) ──")
        lines.append("")
        art = self.generate_pattern(self._current_pattern, min(self._current_width, 40), min(self._current_height, 15))
        for line in art.split("\n"):
            lines.append(f"  {line}")
        return lines
