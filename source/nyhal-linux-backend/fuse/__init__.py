"""
NyFS FUSE Implementation for the Nythera Linux Backend

Implements NPS-017 §4.4 (Storage Guarantees) via a user-space FUSE filesystem.
Provides the copy-on-write, snapshot, checksum, and transparent-compression
guarantees defined in NPS-004 §4.

Per ADR-0016, NyFS is implemented as a user-space FUSE daemon rather than
a kernel module, avoiding kernel-version-specific maintenance and Secure Boot
signing complexity while accepting known FUSE performance overhead.

References:
- NPS-017 §4.4: Storage Guarantees
- NPS-004: NyFS Filesystem Core
- ADR-0016: NyFS Linux Backend implemented as a user-space FUSE filesystem
- ADR-0007: Adopt Zstandard as the default compression codec
"""

__version__ = "0.1.0"
__status__ = "Experimental"

from fuse.nyfs import NyFSFilesystem, NyFSMount

__all__ = [
    "NyFSFilesystem",
    "NyFSMount",
]
