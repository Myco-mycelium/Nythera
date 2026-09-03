from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time
import random


class PieceType(Enum):
    KING = "king"
    QUEEN = "queen"
    ROOK = "rook"
    BISHOP = "bishop"
    KNIGHT = "knight"
    PAWN = "pawn"


class PieceColor(Enum):
    WHITE = "white"
    BLACK = "black"


class GameStatus(Enum):
    ACTIVE = "active"
    CHECK = "check"
    CHECKMATE = "checkmate"
    STALEMATE = "stalemate"
    DRAW = "draw"
    RESIGNED = "resigned"


class AILevel(Enum):
    BEGINNER = 1
    EASY = 3
    MEDIUM = 5
    HARD = 7
    EXPERT = 10


class MoveType(Enum):
    NORMAL = "normal"
    CAPTURE = "capture"
    CASTLE = "castle"
    EN_PASSANT = "en_passant"
    PROMOTION = "promotion"
    CHECK = "check"
    CHECKMATE = "checkmate"


@dataclass
class ChessPiece:
    piece_type: PieceType
    color: PieceColor
    has_moved: bool = False

    @property
    def symbol(self) -> str:
        symbols = {
            (PieceType.KING, PieceColor.WHITE): "♔", (PieceType.QUEEN, PieceColor.WHITE): "♕",
            (PieceType.ROOK, PieceColor.WHITE): "♖", (PieceType.BISHOP, PieceColor.WHITE): "♗",
            (PieceType.KNIGHT, PieceColor.WHITE): "♘", (PieceType.PAWN, PieceColor.WHITE): "♙",
            (PieceType.KING, PieceColor.BLACK): "♚", (PieceType.QUEEN, PieceColor.BLACK): "♛",
            (PieceType.ROOK, PieceColor.BLACK): "♜", (PieceType.BISHOP, PieceColor.BLACK): "♝",
            (PieceType.KNIGHT, PieceColor.BLACK): "♞", (PieceType.PAWN, PieceColor.BLACK): "♟",
        }
        return symbols.get((self.piece_type, self.color), "?")


@dataclass
class ChessMove:
    from_pos: tuple
    to_pos: tuple
    piece: ChessPiece
    captured: Optional[ChessPiece] = None
    move_type: MoveType = MoveType.NORMAL
    notation: str = ""
    is_check: bool = False
    is_checkmate: bool = False

    @property
    def display(self) -> str:
        return f"{self.notation} ({self.move_type.value})"


@dataclass
class Player:
    name: str
    color: PieceColor
    is_ai: bool = False
    ai_level: AILevel = AILevel.MEDIUM
    time_secs: int = 600
    material_value: int = 0


@dataclass
class MoveAnalysis:
    move: ChessMove
    score: float
    eval_text: str
    is_best: bool = False
    alternatives: list = field(default_factory=list)


class ChessEngine:
    def __init__(self):
        self._board: list[list[Optional[ChessPiece]]] = []
        self._current_player: PieceColor = PieceColor.WHITE
        self._status: GameStatus = GameStatus.ACTIVE
        self._move_history: list[ChessMove] = []
        self._player_white: Player = Player("Player", PieceColor.WHITE)
        self._player_black: Player = Player("AI", PieceColor.BLACK, True, AILevel.MEDIUM)
        self._selected_pos: Optional[tuple] = None
        self._legal_moves: list[tuple] = []
        self._captured_white: list[ChessPiece] = []
        self._captured_black: list[ChessPiece] = []
        self._move_number: int = 1
        self._view: str = "board"
        self._create_board()

    def _create_board(self):
        self._board = [[None for _ in range(8)] for _ in range(8)]
        # Black pieces
        back_row = [PieceType.ROOK, PieceType.KNIGHT, PieceType.BISHOP, PieceType.QUEEN,
                    PieceType.KING, PieceType.BISHOP, PieceType.KNIGHT, PieceType.ROOK]
        for c in range(8):
            self._board[0][c] = ChessPiece(back_row[c], PieceColor.BLACK)
            self._board[1][c] = ChessPiece(PieceType.PAWN, PieceColor.BLACK)
        # White pieces
        for c in range(8):
            self._board[7][c] = ChessPiece(back_row[c], PieceColor.WHITE)
            self._board[6][c] = ChessPiece(PieceType.PAWN, PieceColor.WHITE)

    def get_piece(self, row: int, col: int) -> Optional[ChessPiece]:
        if 0 <= row < 8 and 0 <= col < 8:
            return self._board[row][col]
        return None

    def get_legal_moves(self, row: int, col: int) -> list[tuple]:
        piece = self.get_piece(row, col)
        if not piece:
            return []
        moves = []
        if piece.piece_type == PieceType.PAWN:
            direction = -1 if piece.color == PieceColor.WHITE else 1
            if 0 <= row + direction < 8 and self._board[row + direction][col] is None:
                moves.append((row + direction, col))
                if (row == 6 and piece.color == PieceColor.WHITE) or (row == 1 and piece.color == PieceColor.BLACK):
                    if self._board[row + 2 * direction][col] is None:
                        moves.append((row + 2 * direction, col))
            for dc in [-1, 1]:
                nr, nc = row + direction, col + dc
                if 0 <= nr < 8 and 0 <= nc < 8:
                    target = self._board[nr][nc]
                    if target and target.color != piece.color:
                        moves.append((nr, nc))
        elif piece.piece_type == PieceType.KNIGHT:
            for dr, dc in [(-2,-1),(-2,1),(-1,-2),(-1,2),(1,-2),(1,2),(2,-1),(2,1)]:
                nr, nc = row + dr, col + dc
                if 0 <= nr < 8 and 0 <= nc < 8:
                    target = self._board[nr][nc]
                    if target is None or target.color != piece.color:
                        moves.append((nr, nc))
        elif piece.piece_type == PieceType.KING:
            for dr in [-1, 0, 1]:
                for dc in [-1, 0, 1]:
                    if dr == 0 and dc == 0:
                        continue
                    nr, nc = row + dr, col + dc
                    if 0 <= nr < 8 and 0 <= nc < 8:
                        target = self._board[nr][nc]
                        if target is None or target.color != piece.color:
                            moves.append((nr, nc))
        else:
            directions = []
            if piece.piece_type in (PieceType.ROOK, PieceType.QUEEN):
                directions += [(-1,0),(1,0),(0,-1),(0,1)]
            if piece.piece_type in (PieceType.BISHOP, PieceType.QUEEN):
                directions += [(-1,-1),(-1,1),(1,-1),(1,1)]
            for dr, dc in directions:
                for dist in range(1, 8):
                    nr, nc = row + dr * dist, col + dc * dist
                    if 0 <= nr < 8 and 0 <= nc < 8:
                        target = self._board[nr][nc]
                        if target is None:
                            moves.append((nr, nc))
                        elif target.color != piece.color:
                            moves.append((nr, nc))
                            break
                        else:
                            break
        return moves

    def make_move(self, from_row: int, from_col: int, to_row: int, to_col: int) -> str:
        piece = self.get_piece(from_row, from_col)
        if not piece:
            return "No piece at that position"
        if piece.color != self._current_player:
            return "Not your piece!"
        legal = self.get_legal_moves(from_row, from_col)
        if (to_row, to_col) not in legal:
            return "Illegal move!"
        captured = self._board[to_row][to_col]
        move_type = MoveType.CAPTURE if captured else MoveType.NORMAL
        notation = f"{chr(from_col + 97)}{8 - from_row}{chr(to_col + 97)}{8 - to_row}"
        move = ChessMove((from_row, from_col), (to_row, to_col), piece, captured, move_type, notation)
        self._board[to_row][to_col] = piece
        self._board[from_row][from_col] = None
        piece.has_moved = True
        if captured:
            if captured.color == PieceColor.WHITE:
                self._captured_white.append(captured)
            else:
                self._captured_black.append(captured)
        self._move_history.append(move)
        self._current_player = PieceColor.BLACK if self._current_player == PieceColor.WHITE else PieceColor.WHITE
        if self._current_player == PieceColor.WHITE:
            self._move_number += 1
        return f"Moved {piece.piece_type.value} to {chr(to_col + 97)}{8 - to_row}"

    def ai_move(self) -> str:
        if not self._player_black.is_ai:
            return "Not AI turn"
        # Simple random legal move
        for r in range(8):
            for c in range(8):
                piece = self._board[r][c]
                if piece and piece.color == PieceColor.BLACK:
                    moves = self.get_legal_moves(r, c)
                    if moves:
                        tr, tc = random.choice(moves)
                        return self.make_move(r, c, tr, tc)
        return "No moves available"

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                     NYRQIS CHESS ENGINE                                    ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        lines.append(f"  {self._player_white.name} (White) vs {self._player_black.name} (Black)")
        lines.append(f"  Turn: {'White' if self._current_player == PieceColor.WHITE else 'Black'}  Move: {self._move_number}  Status: {self._status.value}")
        lines.append(f"  White: {self._player_white.time_secs}s  Black: {self._player_black.time_secs}s")
        lines.append("")
        lines.append("    a   b   c   d   e   f   g   h")
        lines.append("  ┌───┬───┬───┬───┬───┬───┬───┬───┐")
        for r in range(8):
            row_str = f"{8-r} │"
            for c in range(8):
                piece = self._board[r][c]
                if piece:
                    row_str += f" {piece.symbol} │"
                else:
                    sq = " " if (r + c) % 2 == 0 else "·"
                    row_str += f" {sq} │"
            lines.append(row_str)
            if r < 7:
                lines.append("  ├───┼───┼───┼───┼───┼───┼───┼───┤")
        lines.append("  └───┴───┴───┴───┴───┴───┴───┴───┘")
        lines.append("    a   b   c   d   e   f   g   h")
        lines.append("")
        if self._captured_white:
            caps = " ".join(p.symbol for p in self._captured_white)
            lines.append(f"  Captured by Black: {caps}")
        if self._captured_black:
            caps = " ".join(p.symbol for p in self._captured_black)
            lines.append(f"  Captured by White: {caps}")
        lines.append("")
        lines.append("  ── Recent Moves ──")
        for move in self._move_history[-8:]:
            color = "W" if move.piece.color == PieceColor.WHITE else "B"
            lines.append(f"  {color}: {move.display}")
        lines.append("")
        lines.append("  [M]ove  [H]int  [U]ndo  [N]ew game  [A]I move  [R]esign  [S]ave")
        return lines

    def render_analysis(self) -> list:
        lines = []
        lines.append("  ── Move Analysis ──")
        lines.append("")
        lines.append("  Last move evaluation: +0.3 (slight advantage White)")
        lines.append("  Best move: Nf3 (+0.5)")
        lines.append("  Alternative: e4 (+0.2)")
        lines.append("  Opening: Italian Game")
        return lines
