"""UserAccounts — User account management for Nyrqis.

Provides user account management with:
- User profiles (create, edit, delete)
- Password management (change, strength indicator)
- Avatar selection (built-in + custom)
- Login settings (auto-login, lock timeout)
- Session management
- Apple HIG clean aesthetics

References:
    - ADR-0026: Wayland display-server integration
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class UserType(Enum):
    ADMIN = auto()
    STANDARD = auto()
    GUEST = auto()


class PasswordStrength(Enum):
    VERY_WEAK = auto()
    WEAK = auto()
    FAIR = auto()
    STRONG = auto()
    VERY_STRONG = auto()


class AvatarStyle(Enum):
    COLOR = auto()     # solid color circle
    GRADIENT = auto()  # gradient circle
    INITIALS = auto()  # initials in circle
    IMAGE = auto()     # custom image path


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class UserAvatar:
    """User avatar configuration."""
    style: AvatarStyle = AvatarStyle.COLOR
    color: Tuple[int, int, int] = (80, 140, 255)
    color2: Tuple[int, int, int] = (120, 80, 255)  # for gradient
    initials: str = ""
    image_path: str = ""


@dataclass
class UserProfile:
    """A user profile."""
    id: str
    username: str
    display_name: str
    user_type: UserType = UserType.STANDARD
    avatar: UserAvatar = field(default_factory=UserAvatar)
    home_dir: str = ""
    shell: str = "/bin/nyrqis-shell"
    created_at: float = 0.0
    last_login: float = 0.0
    password_hash: str = ""
    password_changed_at: float = 0.0
    auto_login: bool = False
    lock_timeout: int = 300  # seconds, 0=never
    locked: bool = False

    def __post_init__(self):
        if self.created_at == 0.0:
            self.created_at = time.time()
        if not self.home_dir:
            self.home_dir = f"/home/{self.username}"

    @property
    def display_type(self) -> str:
        return self.user_type.name.capitalize()

    @property
    def initial(self) -> str:
        return (self.display_name or self.username)[0].upper()


@dataclass
class LoginSession:
    """A login session."""
    user_id: str
    started_at: float = 0.0
    last_active: float = 0.0
    tty: str = "tty1"
    display_server: str = "wayland-0"

    def __post_init__(self):
        if self.started_at == 0.0:
            self.started_at = time.time()
        if self.last_active == 0.0:
            self.last_active = time.time()


# ---------------------------------------------------------------------------
# Built-in avatar colors
# ---------------------------------------------------------------------------

AVATAR_COLORS = [
    ((80, 140, 255), (120, 80, 255)),    # Blue-Purple
    ((80, 200, 120), (60, 180, 180)),     # Green-Teal
    ((255, 120, 80), (255, 80, 120)),     # Orange-Red
    ((255, 200, 60), (255, 140, 60)),     # Yellow-Orange
    ((180, 80, 255), (255, 80, 180)),     # Purple-Pink
    ((60, 200, 200), (80, 140, 255)),     # Teal-Blue
    ((200, 80, 80), (255, 120, 80)),      # Red-Orange
    ((100, 200, 100), (60, 200, 160)),    # Green
]


# ---------------------------------------------------------------------------
# UserAccounts
# ---------------------------------------------------------------------------

class UserAccounts:
    """User account management for Nyrqis.

    Handles user profiles, passwords, avatars, and login settings.

    Parameters
    ----------
    width, height : int
        Rendering dimensions.
    """

    def __init__(self, width: int = 400, height: int = 500):
        self.width = width
        self.height = height

        # Users
        self._users: List[UserProfile] = []
        self._current_user_id: Optional[str] = None
        self._active_sessions: List[LoginSession] = []

        # Login settings
        self._auto_login_enabled = False
        self._auto_login_user: Optional[str] = None
        self._lock_screen_timeout = 300  # seconds
        self._require_password = True

        # Initialize default user
        self._init_default_user()

    def _init_default_user(self) -> None:
        """Create the default user account."""
        user = UserProfile(
            id="user-001",
            username="nyrqis",
            display_name="Nyrqis User",
            user_type=UserType.ADMIN,
            avatar=UserAvatar(
                style=AvatarStyle.GRADIENT,
                color=(80, 140, 255),
                color2=(120, 80, 255),
                initials="NU",
            ),
            home_dir="/home/nyrqis",
        )
        user.password_hash = self._hash_password("nyrqis")
        self._users.append(user)
        self._current_user_id = user.id

    # -- User management ------------------------------------------------

    @property
    def users(self) -> List[UserProfile]:
        return list(self._users)

    @property
    def current_user(self) -> Optional[UserProfile]:
        return self.get_user(self._current_user_id)

    def get_user(self, user_id: str) -> Optional[UserProfile]:
        for u in self._users:
            if u.id == user_id:
                return u
        return None

    def get_user_by_name(self, username: str) -> Optional[UserProfile]:
        for u in self._users:
            if u.username == username:
                return u
        return None

    def add_user(self, username: str, display_name: str = "",
                 user_type: UserType = UserType.STANDARD,
                 password: str = "") -> UserProfile:
        """Create a new user account."""
        user_id = f"user-{len(self._users) + 1:03d}"
        color_pair = AVATAR_COLORS[len(self._users) % len(AVATAR_COLORS)]
        user = UserProfile(
            id=user_id,
            username=username,
            display_name=display_name or username.title(),
            user_type=user_type,
            avatar=UserAvatar(
                style=AvatarStyle.GRADIENT,
                color=color_pair[0],
                color2=color_pair[1],
                initials=(display_name or username)[0].upper(),
            ),
            home_dir=f"/home/{username}",
        )
        if password:
            user.password_hash = self._hash_password(password)
        self._users.append(user)
        return user

    def remove_user(self, user_id: str) -> bool:
        """Remove a user account. Cannot remove the current user."""
        if user_id == self._current_user_id:
            return False
        before = len(self._users)
        self._users = [u for u in self._users if u.id != user_id]
        return len(self._users) < before

    def update_user(self, user_id: str, **kwargs) -> bool:
        """Update user profile fields."""
        user = self.get_user(user_id)
        if user is None:
            return False
        for key, value in kwargs.items():
            if hasattr(user, key):
                setattr(user, key, value)
        return True

    def set_user_type(self, user_id: str, user_type: UserType) -> bool:
        user = self.get_user(user_id)
        if user:
            user.user_type = user_type
            return True
        return False

    # -- Password management ---------------------------------------------

    def change_password(self, user_id: str, old_password: str,
                        new_password: str) -> bool:
        """Change a user's password."""
        user = self.get_user(user_id)
        if user is None:
            return False
        if user.password_hash != self._hash_password(old_password):
            return False
        user.password_hash = self._hash_password(new_password)
        user.password_changed_at = time.time()
        return True

    def verify_password(self, user_id: str, password: str) -> bool:
        """Verify a user's password."""
        user = self.get_user(user_id)
        if user is None:
            return False
        return user.password_hash == self._hash_password(password)

    def reset_password(self, user_id: str, new_password: str) -> bool:
        """Reset a user's password (admin action)."""
        user = self.get_user(user_id)
        if user is None:
            return False
        user.password_hash = self._hash_password(new_password)
        user.password_changed_at = time.time()
        return True

    @staticmethod
    def check_password_strength(password: str) -> PasswordStrength:
        """Check password strength."""
        score = 0
        if len(password) >= 8:
            score += 1
        if len(password) >= 12:
            score += 1
        if any(c.isupper() for c in password):
            score += 1
        if any(c.islower() for c in password):
            score += 1
        if any(c.isdigit() for c in password):
            score += 1
        if any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in password):
            score += 1

        strengths = [
            PasswordStrength.VERY_WEAK,
            PasswordStrength.WEAK,
            PasswordStrength.WEAK,
            PasswordStrength.FAIR,
            PasswordStrength.STRONG,
            PasswordStrength.STRONG,
            PasswordStrength.VERY_STRONG,
        ]
        return strengths[min(score, len(strengths) - 1)]

    @staticmethod
    def _hash_password(password: str) -> str:
        """Hash a password (SHA-256 with salt for demo)."""
        salt = "nyrqis-demo-salt"
        return hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()

    # -- Avatar ----------------------------------------------------------

    def set_avatar(self, user_id: str, avatar: UserAvatar) -> bool:
        user = self.get_user(user_id)
        if user:
            user.avatar = avatar
            return True
        return False

    def set_avatar_color(self, user_id: str, color: Tuple[int, int, int],
                         color2: Optional[Tuple[int, int, int]] = None) -> bool:
        user = self.get_user(user_id)
        if user:
            user.avatar.color = color
            if color2:
                user.avatar.color2 = color2
            return True
        return False

    # -- Login settings --------------------------------------------------

    def set_auto_login(self, enabled: bool, username: str = "") -> None:
        self._auto_login_enabled = enabled
        self._auto_login_user = username if enabled else None

    def set_lock_timeout(self, seconds: int) -> None:
        self._lock_screen_timeout = max(0, seconds)

    def lock_user(self, user_id: str) -> bool:
        user = self.get_user(user_id)
        if user:
            user.locked = True
            return True
        return False

    def unlock_user(self, user_id: str, password: str) -> bool:
        user = self.get_user(user_id)
        if user and self.verify_password(user_id, password):
            user.locked = False
            return True
        return False

    # -- Sessions --------------------------------------------------------

    @property
    def active_sessions(self) -> List[LoginSession]:
        return list(self._active_sessions)

    def start_session(self, user_id: str) -> LoginSession:
        session = LoginSession(user_id=user_id)
        self._active_sessions.append(session)
        user = self.get_user(user_id)
        if user:
            user.last_login = time.time()
        return session

    def end_session(self, user_id: str) -> bool:
        before = len(self._active_sessions)
        self._active_sessions = [
            s for s in self._active_sessions if s.user_id != user_id]
        return len(self._active_sessions) < before

    # -- Stats -----------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_users": len(self._users),
            "admins": sum(1 for u in self._users
                         if u.user_type == UserType.ADMIN),
            "standard": sum(1 for u in self._users
                           if u.user_type == UserType.STANDARD),
            "guests": sum(1 for u in self._users
                         if u.user_type == UserType.GUEST),
            "active_sessions": len(self._active_sessions),
            "auto_login": self._auto_login_enabled,
            "lock_timeout": self._lock_screen_timeout,
        }

    # -- Rendering -------------------------------------------------------

    def render(self) -> Tuple[bytes, int, int]:
        """Render the user accounts UI."""
        w, h = self.width, self.height
        buf = bytearray(w * h * 3)
        bg = (30, 30, 40)
        for i in range(0, len(buf), 3):
            buf[i] = bg[0]
            buf[i + 1] = bg[1]
            buf[i + 2] = bg[2]

        # Header
        self._fill_rect(buf, w, 0, 0, w, 48, (42, 42, 56))

        # User cards
        y = 60
        for user in self._users:
            # Card background
            is_current = (user.id == self._current_user_id)
            card_bg = (42, 42, 56) if is_current else (35, 35, 48)
            self._fill_rect(buf, w, 12, y, w - 24, 72, card_bg)

            # Avatar circle
            self._draw_circle(buf, w, 44, y + 36, 22, user.avatar.color)

            # User info placeholder
            name_color = (230, 230, 240) if is_current else (180, 180, 200)
            self._fill_rect(buf, w, 76, y + 12, 120, 14, name_color)
            self._fill_rect(buf, w, 76, y + 32, 80, 12, (150, 150, 170))

            # User type badge
            type_colors = {
                UserType.ADMIN: (255, 200, 60),
                UserType.STANDARD: (80, 140, 255),
                UserType.GUEST: (150, 150, 170),
            }
            badge_color = type_colors.get(user.user_type, (150, 150, 170))
            self._fill_rect(buf, w, 76, y + 50, 50, 12, badge_color)

            # Current indicator
            if is_current:
                self._fill_rect(buf, w, w - 36, y + 28, 12, 12, (80, 200, 120))

            y += 84

        return bytes(buf), w, h

    def _draw_circle(self, buf: bytearray, buf_width: int,
                     cx: int, cy: int, r: int,
                     color: Tuple[int, int, int]) -> None:
        buf_height = len(buf) // (buf_width * 3)
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if dx * dx + dy * dy <= r * r:
                    px, py = cx + dx, cy + dy
                    if 0 <= px < buf_width and 0 <= py < buf_height:
                        idx = (py * buf_width + px) * 3
                        if idx + 2 < len(buf):
                            buf[idx] = color[0]
                            buf[idx + 1] = color[1]
                            buf[idx + 2] = color[2]

    def _fill_rect(self, buf: bytearray, buf_width: int,
                   x: int, y: int, w: int, h: int,
                   color: Tuple[int, int, int]) -> None:
        buf_height = len(buf) // (buf_width * 3)
        for dy in range(h):
            for dx in range(w):
                px, py = x + dx, y + dy
                if 0 <= px < buf_width and 0 <= py < buf_height:
                    idx = (py * buf_width + px) * 3
                    if idx + 2 < len(buf):
                        buf[idx] = color[0]
                        buf[idx + 1] = color[1]
                        buf[idx + 2] = color[2]

    # -- Serialization ---------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "users": [
                {"id": u.id, "username": u.username,
                 "display_name": u.display_name,
                 "type": u.user_type.name,
                 "home": u.home_dir}
                for u in self._users
            ],
            "current_user": self._current_user_id,
            "auto_login": self._auto_login_enabled,
            "lock_timeout": self._lock_screen_timeout,
        }


__all__ = [
    "UserAccounts", "UserProfile", "UserAvatar", "LoginSession",
    "UserType", "PasswordStrength", "AvatarStyle", "AVATAR_COLORS",
]
