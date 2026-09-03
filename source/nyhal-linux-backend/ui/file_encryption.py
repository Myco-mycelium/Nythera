from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time
import hashlib
import math


class EncryptionAlgorithm(Enum):
    AES_256_GCM = "aes-256-gcm"
    AES_256_CBC = "aes-256-cbc"
    AES_128_GCM = "aes-128-gcm"
    CHACHA20_POLY1305 = "chacha20-poly1305"
    XCHACHA20_POLY1305 = "xchacha20-poly1305"


class KeyDerivation(Enum):
    PBKDF2 = "pbkdf2"
    ARGON2ID = "argon2id"
    SCRYPT = "scrypt"
    BCRYPT = "bcrypt"


class FileStatus(Enum):
    UNENCRYPTED = "unencrypted"
    ENCRYPTED = "encrypted"
    ENCRYPTING = "encrypting"
    DECRYPTING = "decrypting"
    FAILED = "failed"
    VERIFIED = "verified"


class IntegrityStatus(Enum):
    VALID = "valid"
    CORRUPTED = "corrupted"
    UNKNOWN = "unknown"
    CHECKING = "checking"


class OperationType(Enum):
    ENCRYPT = "encrypt"
    DECRYPT = "decrypt"
    SIGN = "sign"
    VERIFY = "verify"
    HASH = "hash"


@dataclass
class EncryptedFile:
    name: str
    original_size: int
    encrypted_size: int
    algorithm: EncryptionAlgorithm
    key_derivation: KeyDerivation
    status: FileStatus
    integrity: IntegrityStatus
    timestamp: float
    key_fingerprint: str = ""
    iv: str = ""
    salt: str = ""
    iterations: int = 0
    checksum_sha256: str = ""
    is_starred: bool = False
    tags: list = field(default_factory=list)
    batch_id: str = ""
    error_message: str = ""

    @property
    def display_size(self) -> str:
        size = self.encrypted_size
        if size >= 1024 * 1024 * 1024:
            return f"{size / (1024 ** 3):.2f} GB"
        if size >= 1024 * 1024:
            return f"{size / (1024 ** 2):.1f} MB"
        if size >= 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size} B"

    @property
    def overhead_percent(self) -> str:
        if self.original_size == 0:
            return "0%"
        overhead = ((self.encrypted_size - self.original_size) / self.original_size) * 100
        return f"+{overhead:.1f}%"

    @property
    def filename(self) -> str:
        return self.name.split("/")[-1]


@dataclass
class EncryptionKey:
    name: str
    algorithm: EncryptionAlgorithm
    key_size: int
    fingerprint: str
    created_at: float
    expires_at: float = 0
    is_master: bool = False
    is_revoked: bool = False

    @property
    def age_days(self) -> int:
        return int((time.time() - self.created_at) / 86400)

    @property
    def is_expired(self) -> bool:
        return self.expires_at > 0 and time.time() > self.expires_at


@dataclass
class OperationLog:
    operation: OperationType
    filename: str
    timestamp: float
    success: bool
    duration_ms: int = 0
    error: str = ""


class FileEncryption:
    def __init__(self):
        self._files: list[EncryptedFile] = []
        self._selected_file: int = 0
        self._keys: list[EncryptionKey] = []
        self._selected_key: int = 0
        self._operation_log: list[OperationLog] = []
        self._default_algorithm: EncryptionAlgorithm = EncryptionAlgorithm.AES_256_GCM
        self._default_kdf: KeyDerivation = KeyDerivation.ARGON2ID
        self._default_iterations: int = 100_000
        self._batch_mode: bool = False
        self._batch_files: list[str] = []
        self._verify_after: bool = True
        self._view: str = "files"
        self._create_samples()

    def _create_samples(self):
        now = time.time()
        self._files = [
            EncryptedFile("/home/user/Documents/tax-return-2025.pdf", 2_457_600, 2_457_856,
                          EncryptionAlgorithm.AES_256_GCM, KeyDerivation.ARGON2ID, FileStatus.ENCRYPTED,
                          IntegrityStatus.VALID, now - 86400 * 30, "AA:BB:CC:DD:EE:FF:01:02",
                          iv="7f3a9b2c...", salt="d4e5f6a7...", iterations=100_000,
                          checksum_sha256="a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2",
                          is_starred=True, tags=["finance", "taxes"]),
            EncryptedFile("/home/user/Projects/secrets.env", 4_096, 4_352,
                          EncryptionAlgorithm.CHACHA20_POLY1305, KeyDerivation.SCRYPT, FileStatus.ENCRYPTED,
                          IntegrityStatus.VALID, now - 86400 * 7, "AA:BB:CC:DD:EE:FF:03:04",
                          iv="9e8d7c6b...", salt="5a4b3c2d...", iterations=65_536,
                          checksum_sha256="b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3",
                          tags=["secrets", "env"]),
            EncryptedFile("/home/user/Videos/private-clip.mp4", 157_286_400, 157_286_656,
                          EncryptionAlgorithm.AES_256_CBC, KeyDerivation.PBKDF2, FileStatus.ENCRYPTED,
                          IntegrityStatus.CORRUPTED, now - 86400 * 60, "AA:BB:CC:DD:EE:FF:05:06",
                          iterations=400_000, error_message="Integrity check failed - possible tampering",
                          tags=["video", "private"]),
            EncryptedFile("/home/user/Downloads/file.zip", 52_428_800, 52_429_056,
                          EncryptionAlgorithm.AES_256_GCM, KeyDerivation.ARGON2ID, FileStatus.UNENCRYPTED,
                          IntegrityStatus.UNKNOWN, now - 3600, tags=["download"]),
            EncryptedFile("/home/user/Documents/medical-records.pdf", 8_388_608, 8_388_864,
                          EncryptionAlgorithm.XCHACHA20_POLY1305, KeyDerivation.ARGON2ID, FileStatus.ENCRYPTED,
                          IntegrityStatus.VALID, now - 86400 * 14, "AA:BB:CC:DD:EE:FF:07:08",
                          iv="1a2b3c4d...", salt="5e6f7a8b...", iterations=100_000,
                          checksum_sha256="c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4",
                          is_starred=True, tags=["medical", "sensitive"]),
            EncryptedFile("/home/user/Backup/system-backup.tar.gz", 1_073_741_824, 1_073_742_080,
                          EncryptionAlgorithm.AES_256_GCM, KeyDerivation.ARGON2ID, FileStatus.ENCRYPTED,
                          IntegrityStatus.VALID, now - 86400 * 5, "AA:BB:CC:DD:EE:FF:09:10",
                          iv="ab12cd34...", salt="ef56gh78...", iterations=100_000,
                          checksum_sha256="d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5",
                          tags=["backup", "system"]),
        ]

        self._keys = [
            EncryptionKey("Master Key - Primary", EncryptionAlgorithm.AES_256_GCM, 256, "AA:BB:CC:DD:EE:FF:01:02", now - 86400 * 365, is_master=True),
            EncryptionKey("SSH Key - nyrqis-dev", EncryptionAlgorithm.CHACHA20_POLY1305, 256, "AA:BB:CC:DD:EE:FF:03:04", now - 86400 * 180, expires_at=now + 86400 * 185),
            EncryptionKey("Backup Key", EncryptionAlgorithm.AES_256_GCM, 256, "AA:BB:CC:DD:EE:FF:09:10", now - 86400 * 30),
            EncryptionKey("Revoked Key - Old", EncryptionAlgorithm.AES_128_GCM, 128, "AA:BB:CC:DD:EE:FF:FF:FF", now - 86400 * 500, is_revoked=True),
        ]

        self._operation_log = [
            OperationLog(OperationType.ENCRYPT, "/home/user/Documents/medical-records.pdf", now - 86400 * 14, True, 1250),
            OperationLog(OperationType.ENCRYPT, "/home/user/Backup/system-backup.tar.gz", now - 86400 * 5, True, 45000),
            OperationLog(OperationType.VERIFY, "/home/user/Videos/private-clip.mp4", now - 3600, False, 500, "Integrity check failed"),
            OperationLog(OperationType.DECRYPT, "/tmp/test-file.txt", now - 7200, True, 350),
            OperationLog(OperationType.VERIFY, "/home/user/Documents/tax-return-2025.pdf", now - 1800, True, 800),
        ]

    @property
    def selected_file(self) -> Optional[EncryptedFile]:
        if 0 <= self._selected_file < len(self._files):
            return self._files[self._selected_file]
        return None

    @property
    def selected_key(self) -> Optional[EncryptionKey]:
        if 0 <= self._selected_key < len(self._keys):
            return self._keys[self._selected_key]
        return None

    @property
    def total_files(self) -> int:
        return len(self._files)

    @property
    def encrypted_count(self) -> int:
        return sum(1 for f in self._files if f.status == FileStatus.ENCRYPTED)

    @property
    def corrupted_count(self) -> int:
        return sum(1 for f in self._files if f.integrity == IntegrityStatus.CORRUPTED)

    @property
    def total_size(self) -> int:
        return sum(f.encrypted_size for f in self._files)

    @property
    def total_size_display(self) -> str:
        s = self.total_size
        if s >= 1_073_741_824:
            return f"{s / 1_073_741_824:.2f} GB"
        if s >= 1_048_576:
            return f"{s / 1_048_576:.1f} MB"
        return f"{s / 1024:.1f} KB"

    def select_file(self, idx: int):
        if 0 <= idx < len(self._files):
            self._selected_file = idx

    def select_key(self, idx: int):
        if 0 <= idx < len(self._keys):
            self._selected_key = idx

    def encrypt_file(self, file_idx: int, key_idx: int = 0) -> bool:
        if 0 <= file_idx < len(self._files):
            f = self._files[file_idx]
            if f.status == FileStatus.UNENCRYPTED:
                f.status = FileStatus.ENCRYPTED
                f.integrity = IntegrityStatus.VALID
                self._operation_log.append(OperationLog(OperationType.ENCRYPT, f.filename, time.time(), True, 500))
                return True
        return False

    def decrypt_file(self, file_idx: int) -> bool:
        if 0 <= file_idx < len(self._files):
            f = self._files[file_idx]
            if f.status == FileStatus.ENCRYPTED:
                self._operation_log.append(OperationLog(OperationType.DECRYPT, f.filename, time.time(), True, 300))
                return True
        return False

    def verify_file(self, file_idx: int) -> bool:
        if 0 <= file_idx < len(self._files):
            f = self._files[file_idx]
            self._operation_log.append(OperationLog(OperationType.VERIFY, f.filename, time.time(), f.integrity == IntegrityStatus.VALID, 800))
            return True
        return False

    @staticmethod
    def compute_checksum(data: str, algorithm: str = "sha256") -> str:
        h = hashlib.new(algorithm)
        h.update(data.encode())
        return h.hexdigest()

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS FILE ENCRYPTION TOOL                             ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        lines.append(f"  Files: {self.total_files}  Encrypted: {self.encrypted_count}  ⚠️ Corrupted: {self.corrupted_count}  Total: {self.total_size_display}")
        lines.append(f"  Algorithm: {self._default_algorithm.value}  KDF: {self._default_kdf.value}  Iterations: {self._default_iterations:,}")
        lines.append("")
        status_icons = {"encrypted": "🔐", "unencrypted": "📄", "encrypting": "⚙️", "failed": "❌", "verified": "✅"}
        integrity_icons = {"valid": "✅", "corrupted": "💥", "unknown": "❓", "checking": "🔍"}
        for i, f in enumerate(self._files):
            sel = "▶" if i == self._selected_file else " "
            s_icon = status_icons.get(f.status.value, "?")
            i_icon = integrity_icons.get(f.integrity.value, "?")
            star = "⭐" if f.is_starred else ""
            lines.append(f"  {sel} {s_icon}{i_icon} {f.filename}{star}")
            lines.append(f"    {f.display_size} ({f.overhead_percent})  {f.algorithm.value}  {f.key_fingerprint or 'no key'}")
        lines.append("")
        lines.append("  ── Keys ──")
        for i, k in enumerate(self._keys):
            sel = "▶" if i == self._selected_key else " "
            status = "🔴 REVOKED" if k.is_revoked else ("⏰ EXPIRED" if k.is_expired else "🟢")
            master = " 👑" if k.is_master else ""
            lines.append(f"  {sel} {status}{master} {k.name}  {k.key_size}-bit  {k.age_days}d old")
        lines.append("")
        lines.append("  ── Recent Operations ──")
        for op in self._operation_log[-4:]:
            icon = "✅" if op.success else "❌"
            lines.append(f"  {icon} {op.operation.value} {op.filename}  {op.duration_ms}ms")
        lines.append("")
        lines.append("  [E]ncrypt  [D]ecrypt  [V]erify  [K]ey  [B]atch  [L]og  [S]tar")
        return lines

    def render_file_detail(self) -> list:
        f = self.selected_file
        if not f:
            return ["  No file selected"]
        lines = []
        lines.append(f"  ── {f.filename} ──")
        lines.append(f"  Path: {f.name}")
        lines.append(f"  Original Size: {EncryptedFile('', f.original_size, 0, None, None, None, None, 0).display_size}")
        lines.append(f"  Encrypted Size: {f.display_size} (+{f.overhead_percent})")
        lines.append(f"  Algorithm: {f.algorithm.value}")
        lines.append(f"  KDF: {f.key_derivation.value}  Iterations: {f.iterations:,}")
        lines.append(f"  IV: {f.iv or 'N/A'}")
        lines.append(f"  Salt: {f.salt or 'N/A'}")
        lines.append(f"  Key Fingerprint: {f.key_fingerprint or 'N/A'}")
        lines.append(f"  Status: {f.status.value}")
        lines.append(f"  Integrity: {f.integrity.value}")
        if f.checksum_sha256:
            lines.append(f"  SHA-256: {f.checksum_sha256[:48]}...")
        if f.tags:
            lines.append(f"  Tags: {', '.join(f.tags)}")
        if f.error_message:
            lines.append(f"  ⚠️ {f.error_message}")
        return lines

    def render_keys(self) -> list:
        lines = []
        lines.append("  ── Encryption Keys ──")
        lines.append("")
        for i, k in enumerate(self._keys):
            sel = "▶" if i == self._selected_key else " "
            status = "🔴 REVOKED" if k.is_revoked else ("⏰ EXPIRED" if k.is_expired else "🟢 Active")
            lines.append(f"  {sel} {status}  {k.name}")
            lines.append(f"    {k.algorithm.value}  {k.key_size}-bit  FP: {k.fingerprint}")
            lines.append(f"    Age: {k.age_days}d  {'👑 Master' if k.is_master else ''}")
        return lines

    def render_log(self) -> list:
        lines = []
        lines.append("  ── Operation Log ──")
        lines.append("")
        for op in self._operation_log:
            icon = "✅" if op.success else "❌"
            age = int((time.time() - op.timestamp) / 3600)
            lines.append(f"  {icon} {op.operation.value.upper():8s} {op.filename:<40s} {op.duration_ms:>6d}ms  {age}h ago")
            if op.error:
                lines.append(f"    ⚠️ {op.error}")
        return lines
