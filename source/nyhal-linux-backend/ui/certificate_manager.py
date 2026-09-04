"""
Nyrqis OS - Certificate Manager
SSL/TLS certificate display, expiry tracking, and generation.

Features:
- Certificate store with local, system, and browser certs
- Expiry tracking with warnings
- Certificate details (subject, issuer, serial, SANs, key info)
- Self-signed certificate generation
- CSR creation
- Certificate chain verification
- Import/export operations
- Trust store management
"""

import time
import hashlib
import random
import secrets
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


class CertType(Enum):
    LEAF = "leaf"  # End-entity / server cert
    INTERMEDIATE = "intermediate"
    ROOT = "root"
    SELF_SIGNED = "self-signed"
    CLIENT = "client"


class CertStatus(Enum):
    VALID = "valid"
    EXPIRED = "expired"
    EXPIRING_SOON = "expiring_soon"
    REVOKED = "revoked"
    UNTRUSTED = "untrusted"
    BROKEN = "broken"


class KeyAlgorithm(Enum):
    RSA_2048 = "RSA 2048-bit"
    RSA_4096 = "RSA 4096-bit"
    ECDSA_P256 = "ECDSA P-256"
    ECDSA_P384 = "ECDSA P-384"
    ED25519 = "Ed25519"


class SignatureAlgorithm(Enum):
    SHA256_RSA = "SHA256withRSA"
    SHA384_RSA = "SHA384withRSA"
    SHA512_RSA = "SHA512withRSA"
    SHA256_ECDSA = "SHA256withECDSA"
    SHA384_ECDSA = "SHA384withECDSA"


class TrustStore(Enum):
    SYSTEM = "System"
    USER = "User"
    BROWSER = "Browser"
    NSS = "NSS"
    CUSTOM = "Custom"


CERT_TYPE_ICONS = {
    CertType.LEAF: "📜",
    CertType.INTERMEDIATE: "📋",
    CertType.ROOT: "🏛️",
    CertType.SELF_SIGNED: "✍️",
    CertType.CLIENT: "👤",
}

STATUS_ICONS = {
    CertStatus.VALID: "🟢",
    CertStatus.EXPIRED: "🔴",
    CertStatus.EXPIRING_SOON: "🟡",
    CertStatus.REVOKED: "🚫",
    CertStatus.UNTRUSTED: "⚠️",
    CertStatus.BROKEN: "❌",
}


@dataclass
class Certificate:
    id: int = 0
    common_name: str = ""
    organization: str = ""
    cert_type: CertType = CertType.LEAF
    status: CertStatus = CertStatus.VALID

    # Subject fields
    country: str = ""
    state: str = ""
    locality: str = ""
    org_unit: str = ""
    email: str = ""

    # Validity
    issued: float = 0.0
    expires: float = 0.0

    # Technical details
    serial: str = ""
    fingerprint_sha256: str = ""
    key_algorithm: KeyAlgorithm = KeyAlgorithm.RSA_2048
    sig_algorithm: SignatureAlgorithm = SignatureAlgorithm.SHA256_RSA
    key_size: int = 2048

    # SANs
    san_dns: List[str] = field(default_factory=list)
    san_ip: List[str] = field(default_factory=list)

    # Issuer chain
    issuer_cn: str = ""
    issuer_org: str = ""
    is_ca: bool = False
    path_length: int = 0

    # Trust
    trust_store: TrustStore = TrustStore.SYSTEM
    trusted: bool = True

    # PEM data (simplified)
    pem_data: str = ""

    @property
    def type_icon(self) -> str:
        return CERT_TYPE_ICONS.get(self.cert_type, "❓")

    @property
    def status_icon(self) -> str:
        return STATUS_ICONS.get(self.status, "❓")

    @property
    def days_until_expiry(self) -> int:
        if self.expires == 0:
            return 9999
        return max(0, int((self.expires - time.time()) / 86400))

    @property
    def expiry_str(self) -> str:
        if self.expires == 0:
            return "N/A"
        return time.strftime("%Y-%m-%d", time.localtime(self.expires))

    @property
    def issued_str(self) -> str:
        if self.issued == 0:
            return "N/A"
        return time.strftime("%Y-%m-%d", time.localtime(self.issued))

    @property
    def validity_bar(self) -> str:
        total_days = max(1, (self.expires - self.issued) / 86400) if self.expires and self.issued else 365
        remaining = self.days_until_expiry
        ratio = min(1.0, remaining / total_days)
        filled = int(ratio * 20)
        return "█" * filled + "░" * (20 - filled)

    @property
    def subject_cn(self) -> str:
        return f"CN={self.common_name}"

    @property
    def subject_full(self) -> str:
        parts = [f"CN={self.common_name}"]
        if self.org_unit:
            parts.append(f"OU={self.org_unit}")
        if self.organization:
            parts.append(f"O={self.organization}")
        if self.locality:
            parts.append(f"L={self.locality}")
        if self.state:
            parts.append(f"ST={self.state}")
        if self.country:
            parts.append(f"C={self.country}")
        return ", ".join(parts)

    @property
    def issuer_display(self) -> str:
        if self.issuer_org:
            return f"{self.issuer_cn} ({self.issuer_org})"
        return self.issuer_cn

    @property
    def key_display(self) -> str:
        return f"{self.key_algorithm.value} ({self.key_size}-bit)"

    @property
    def fingerprint_display(self) -> str:
        if not self.fingerprint_sha256:
            return "N/A"
        return self.fingerprint_sha256[:32] + "..."

    @property
    def san_count(self) -> int:
        return len(self.san_dns) + len(self.san_ip)

    @property
    def trust_icon(self) -> str:
        if not self.trusted:
            return "🚫"
        icons = {
            TrustStore.SYSTEM: "🖥️",
            TrustStore.USER: "👤",
            TrustStore.BROWSER: "🌐",
            TrustStore.NSS: "📦",
            TrustStore.CUSTOM: "🔧",
        }
        return icons.get(self.trust_store, "❓")


@dataclass
class CSRRequest:
    common_name: str = ""
    organization: str = ""
    country: str = ""
    state: str = ""
    locality: str = ""
    key_algorithm: KeyAlgorithm = KeyAlgorithm.RSA_2048
    san_dns: List[str] = field(default_factory=list)
    san_ip: List[str] = field(default_factory=list)
    created: float = 0.0

    @property
    def subject(self) -> str:
        parts = [f"CN={self.common_name}"]
        if self.organization:
            parts.append(f"O={self.organization}")
        if self.country:
            parts.append(f"C={self.country}")
        return ", ".join(parts)


@dataclass
class CertChain:
    name: str = ""
    certificates: List[Certificate] = field(default_factory=list)
    valid: bool = True

    @property
    def length(self) -> int:
        return len(self.certificates)

    @property
    def leaf(self) -> Optional[Certificate]:
        return self.certificates[0] if self.certificates else None

    @property
    def root(self) -> Optional[Certificate]:
        return self.certificates[-1] if self.certificates else None

    @property
    def status_icon(self) -> str:
        return "🟢" if self.valid else "🔴"


@dataclass
class CertEvent:
    timestamp: float = 0.0
    event_type: str = ""  # issued, renewed, revoked, exported, imported
    cert_name: str = ""
    details: str = ""

    @property
    def time_str(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.timestamp))

    @property
    def icon(self) -> str:
        icons = {
            "issued": "📜", "renewed": "🔄", "revoked": "🚫",
            "exported": "📤", "imported": "📥",
        }
        return icons.get(self.event_type, "❓")


class CertificateManager:
    def __init__(self):
        self.certificates: List[Certificate] = []
        self.chains: List[CertChain] = []
        self.csrs: List[CSRRequest] = []
        self.events: List[CertEvent] = []
        self._selected_cert: int = 0
        self._selected_chain: int = 0
        self._view_mode: str = "certificates"
        self._cert_counter: int = 0
        self._create_sample_data()

    def _create_sample_data(self):
        now = time.time()
        day = 86400

        self.certificates = [
            Certificate(
                id=1, common_name="nyrqis.dev", organization="Nyrqis OS",
                cert_type=CertType.LEAF, status=CertStatus.VALID,
                country="US", state="California", locality="San Francisco",
                org_unit="Engineering",
                issued=now - 90 * day, expires=now + 275 * day,
                serial="0A:1B:2C:3D:4E:5F",
                fingerprint_sha256=hashlib.sha256(b"nyrqis.dev").hexdigest(),
                key_algorithm=KeyAlgorithm.ECDSA_P256,
                sig_algorithm=SignatureAlgorithm.SHA256_ECDSA,
                key_size=256,
                san_dns=["nyrqis.dev", "www.nyrqis.dev", "api.nyrqis.dev", "docs.nyrqis.dev"],
                san_ip=["192.168.1.10"],
                issuer_cn="Let's Encrypt Authority X3", issuer_org="Let's Encrypt",
                trust_store=TrustStore.SYSTEM,
            ),
            Certificate(
                id=2, common_name="*.internal.nyrqis.dev", organization="Nyrqis OS",
                cert_type=CertType.LEAF, status=CertStatus.VALID,
                country="US", state="California", locality="San Francisco",
                issued=now - 45 * day, expires=now + 320 * day,
                serial="AA:BB:CC:DD:EE:01",
                fingerprint_sha256=hashlib.sha256(b"internal").hexdigest(),
                key_algorithm=KeyAlgorithm.RSA_4096,
                key_size=4096,
                san_dns=["*.internal.nyrqis.dev", "internal.nyrqis.dev"],
                issuer_cn="Nyrqis Internal CA", issuer_org="Nyrqis OS",
                trust_store=TrustStore.SYSTEM,
            ),
            Certificate(
                id=3, common_name="Nyrqis Root CA", organization="Nyrqis OS",
                cert_type=CertType.ROOT, status=CertStatus.VALID,
                country="US", state="California", locality="San Francisco",
                issued=now - 365 * day, expires=now + 3285 * day,
                serial="00:11:22:33:44:55",
                fingerprint_sha256=hashlib.sha256(b"root-ca").hexdigest(),
                key_algorithm=KeyAlgorithm.RSA_4096,
                key_size=4096,
                issuer_cn="Nyrqis Root CA", issuer_org="Nyrqis OS",
                is_ca=True, path_length=2,
                trust_store=TrustStore.SYSTEM,
            ),
            Certificate(
                id=4, common_name="Nyrqis Intermediate CA", organization="Nyrqis OS",
                cert_type=CertType.INTERMEDIATE, status=CertStatus.VALID,
                country="US", state="California", locality="San Francisco",
                issued=now - 200 * day, expires=now + 165 * day,
                serial="AA:00:BB:11:CC:22",
                fingerprint_sha256=hashlib.sha256(b"inter-ca").hexdigest(),
                key_algorithm=KeyAlgorithm.RSA_2048,
                key_size=2048,
                issuer_cn="Nyrqis Root CA", issuer_org="Nyrqis OS",
                is_ca=True, path_length=1,
                trust_store=TrustStore.SYSTEM,
            ),
            Certificate(
                id=5, common_name="localhost", organization="Nyrqis OS",
                cert_type=CertType.SELF_SIGNED, status=CertStatus.VALID,
                country="US", state="California", locality="San Francisco",
                issued=now - 10 * day, expires=now + 355 * day,
                serial="FF:00:FF:00:FF:00",
                fingerprint_sha256=hashlib.sha256(b"localhost").hexdigest(),
                key_algorithm=KeyAlgorithm.ECDSA_P256,
                key_size=256,
                san_dns=["localhost", "127.0.0.1", "::1"],
                san_ip=["127.0.0.1", "::1"],
                issuer_cn="localhost", issuer_org="",
                trust_store=TrustStore.USER,
            ),
            Certificate(
                id=6, common_name="expired.nyrqis.dev", organization="Nyrqis OS",
                cert_type=CertType.LEAF, status=CertStatus.EXPIRED,
                issued=now - 730 * day, expires=now - 30 * day,
                serial="DE:AD:BE:EF:00:01",
                fingerprint_sha256=hashlib.sha256(b"expired").hexdigest(),
                key_algorithm=KeyAlgorithm.RSA_2048,
                key_size=2048,
                san_dns=["expired.nyrqis.dev"],
                issuer_cn="Let's Encrypt Authority X3", issuer_org="Let's Encrypt",
                trust_store=TrustStore.SYSTEM,
            ),
            Certificate(
                id=7, common_name="api-dev.nyrqis.dev", organization="Nyrqis OS",
                cert_type=CertType.LEAF, status=CertStatus.EXPIRING_SOON,
                issued=now - 350 * day, expires=now + 15 * day,
                serial="CA:FE:BA:BE:00:01",
                fingerprint_sha256=hashlib.sha256(b"expiring").hexdigest(),
                key_algorithm=KeyAlgorithm.ECDSA_P256,
                key_size=256,
                san_dns=["api-dev.nyrqis.dev", "staging.nyrqis.dev"],
                issuer_cn="Let's Encrypt Authority X3", issuer_org="Let's Encrypt",
                trust_store=TrustStore.SYSTEM,
            ),
            Certificate(
                id=8, common_name="revoked-cert.nyrqis.dev", organization="Nyrqis OS",
                cert_type=CertType.LEAF, status=CertStatus.REVOKED,
                issued=now - 180 * day, expires=now + 185 * day,
                serial="BA:AD:CA:FE:00:02",
                fingerprint_sha256=hashlib.sha256(b"revoked").hexdigest(),
                key_algorithm=KeyAlgorithm.RSA_2048,
                key_size=2048,
                san_dns=["revoked-cert.nyrqis.dev"],
                issuer_cn="Let's Encrypt Authority X3", issuer_org="Let's Encrypt",
                trust_store=TrustStore.SYSTEM,
            ),
            Certificate(
                id=9, common_name="Nyrqis Client Cert", organization="Nyrqis OS",
                cert_type=CertType.CLIENT, status=CertStatus.VALID,
                issued=now - 60 * day, expires=now + 305 * day,
                serial="CE:01:CE:02:CE:03",
                fingerprint_sha256=hashlib.sha256(b"client-cert").hexdigest(),
                key_algorithm=KeyAlgorithm.ECDSA_P256,
                key_size=256,
                email="dev@nyrqis.dev",
                issuer_cn="Nyrqis Intermediate CA", issuer_org="Nyrqis OS",
                trust_store=TrustStore.USER,
            ),
            Certificate(
                id=10, common_name="old-ca.nyrqis.dev", organization="Nyrqis OS",
                cert_type=CertType.ROOT, status=CertStatus.EXPIRED,
                issued=now - 2000 * day, expires=now - 635 * day,
                serial="11:22:33:44:55:66",
                fingerprint_sha256=hashlib.sha256(b"old-root").hexdigest(),
                key_algorithm=KeyAlgorithm.RSA_2048,
                key_size=2048,
                issuer_cn="old-ca.nyrqis.dev", issuer_org="Nyrqis OS",
                is_ca=True, trust_store=TrustStore.CUSTOM,
            ),
        ]
        self._cert_counter = 11

        # Chains
        leaf = self.certificates[0]
        intermediate = self.certificates[3]
        root = self.certificates[2]
        self.chains = [
            CertChain("nyrqis.dev chain", [leaf, intermediate, root], True),
            CertChain("expired chain", [self.certificates[5], intermediate, root], False),
            CertChain("expiring soon chain", [self.certificates[6], intermediate, root], True),
        ]

        # CSRs
        self.csrs = [
            CSRRequest("new-service.nyrqis.dev", "Nyrqis OS", "US", "California", "San Francisco",
                       KeyAlgorithm.ECDSA_P256, ["new-service.nyrqis.dev"], created=now - 86400),
            CSRRequest("mail.nyrqis.dev", "Nyrqis OS", "US", "California", "San Francisco",
                       KeyAlgorithm.RSA_2048, ["mail.nyrqis.dev", "imap.nyrqis.dev"], created=now - 172800),
        ]

        # Events
        self.events = [
            CertEvent(now - 90 * day, "issued", "nyrqis.dev", "New LE certificate"),
            CertEvent(now - 85 * day, "renewed", "staging.nyrqis.dev", "Auto-renewed"),
            CertEvent(now - 45 * day, "issued", "*.internal.nyrqis.dev", "Internal CA signed"),
            CertEvent(now - 30 * day, "revoked", "revoked-cert.nyrqis.dev", "Key compromise"),
            CertEvent(now - 20 * day, "exported", "localhost", "PEM exported"),
            CertEvent(now - 10 * day, "imported", "mail.nyrqis.dev", "PEM imported"),
            CertEvent(now - 5 * day, "renewed", "api-dev.nyrqis.dev", "Manual renewal"),
        ]

    # ─── Navigation ────────────────────────────────────────────────────

    @property
    def selected_cert(self) -> Optional[Certificate]:
        if 0 <= self._selected_cert < len(self.certificates):
            return self.certificates[self._selected_cert]
        return None

    def select_cert(self, idx: int):
        if 0 <= idx < len(self.certificates):
            self._selected_cert = idx

    def set_view(self, view: str):
        self._view_mode = view

    def select_down(self):
        if self._view_mode == "certificates":
            self._selected_cert = min(self._selected_cert + 1, len(self.certificates) - 1)
        elif self._view_mode == "chains":
            self._selected_chain = min(self._selected_chain + 1, len(self.chains) - 1)

    def select_up(self):
        if self._view_mode == "certificates":
            self._selected_cert = max(self._selected_cert - 1, 0)
        elif self._view_mode == "chains":
            self._selected_chain = max(self._selected_chain - 1, 0)

    # ─── Certificate Actions ───────────────────────────────────────────

    def generate_self_signed(self, cn: str, org: str = "", days: int = 365,
                              key_algo: KeyAlgorithm = KeyAlgorithm.ECDSA_P256) -> Certificate:
        now = time.time()
        key_size = 256 if "ECDSA" in key_algo.value or "Ed25519" in key_algo.value else 2048
        serial = secrets.token_hex(6).upper()
        serial_fmt = ":".join(serial[i:i+2] for i in range(0, 12, 2))
        fp = hashlib.sha256(cn.encode()).hexdigest()

        cert = Certificate(
            id=self._cert_counter, common_name=cn, organization=org,
            cert_type=CertType.SELF_SIGNED, status=CertStatus.VALID,
            issued=now, expires=now + days * 86400,
            serial=serial_fmt, fingerprint_sha256=fp,
            key_algorithm=key_algo, key_size=key_size,
            san_dns=[cn], issuer_cn=cn, issuer_org=org,
            trust_store=TrustStore.USER,
        )
        self._cert_counter += 1
        self.certificates.append(cert)
        self.events.insert(0, CertEvent(now, "issued", cn, "Self-signed certificate generated"))
        return cert

    def create_csr(self, cn: str, org: str = "", country: str = "",
                    key_algo: KeyAlgorithm = KeyAlgorithm.RSA_2048,
                    san_dns: List[str] = None) -> CSRRequest:
        csr = CSRRequest(
            common_name=cn, organization=org, country=country,
            key_algorithm=key_algo, san_dns=san_dns or [cn],
            created=time.time(),
        )
        self.csrs.append(csr)
        return csr

    def renew_cert(self, cert_idx: int) -> Optional[Certificate]:
        if 0 <= cert_idx < len(self.certificates):
            old = self.certificates[cert_idx]
            now = time.time()
            new_cert = Certificate(
                id=self._cert_counter, common_name=old.common_name,
                organization=old.organization, cert_type=old.cert_type,
                status=CertStatus.VALID, country=old.country,
                state=old.state, locality=old.locality,
                issued=now, expires=now + 365 * 86400,
                serial=secrets.token_hex(6).upper(),
                fingerprint_sha256=hashlib.sha256(old.common_name.encode()).hexdigest(),
                key_algorithm=old.key_algorithm, key_size=old.key_size,
                san_dns=old.san_dns.copy(), san_ip=old.san_ip.copy(),
                issuer_cn=old.issuer_cn, issuer_org=old.issuer_org,
                trust_store=old.trust_store,
            )
            new_cert.serial = ":".join(new_cert.serial[i:i+2] for i in range(0, 12, 2))
            self._cert_counter += 1
            self.certificates.append(new_cert)
            self.events.insert(0, CertEvent(now, "renewed", old.common_name, "Certificate renewed"))
            return new_cert
        return None

    def revoke_cert(self, cert_idx: int) -> bool:
        if 0 <= cert_idx < len(self.certificates):
            cert = self.certificates[cert_idx]
            if cert.status != CertStatus.REVOKED:
                cert.status = CertStatus.REVOKED
                self.events.insert(0, CertEvent(time.time(), "revoked", cert.common_name, "Certificate revoked"))
                return True
        return False

    def delete_cert(self, cert_idx: int) -> bool:
        if 0 <= cert_idx < len(self.certificates):
            cert = self.certificates[cert_idx]
            self.certificates.pop(cert_idx)
            if self._selected_cert >= len(self.certificates):
                self._selected_cert = max(0, len(self.certificates) - 1)
            self.events.insert(0, CertEvent(time.time(), "revoked", cert.common_name, "Certificate deleted"))
            return True
        return False

    def toggle_trust(self, cert_idx: int) -> bool:
        if 0 <= cert_idx < len(self.certificates):
            cert = self.certificates[cert_idx]
            cert.trusted = not cert.trusted
            cert.status = CertStatus.VALID if cert.trusted else CertStatus.UNTRUSTED
            return True
        return False

    def verify_chain(self, chain_idx: int) -> bool:
        if 0 <= chain_idx < len(self.chains):
            chain = self.chains[chain_idx]
            all_valid = all(c.status == CertStatus.VALID for c in chain.certificates)
            chain.valid = all_valid
            return all_valid
        return False

    def export_cert(self, cert_idx: int) -> str:
        if 0 <= cert_idx < len(self.certificates):
            cert = self.certificates[cert_idx]
            self.events.insert(0, CertEvent(time.time(), "exported", cert.common_name, "PEM exported"))
            return f"-----BEGIN CERTIFICATE-----\n<base64 data>\n-----END CERTIFICATE-----"
        return ""

    def import_cert(self, cn: str, org: str = "") -> Certificate:
        now = time.time()
        cert = Certificate(
            id=self._cert_counter, common_name=cn, organization=org,
            cert_type=CertType.LEAF, status=CertStatus.VALID,
            issued=now, expires=now + 365 * 86400,
            serial=secrets.token_hex(6).upper(),
            fingerprint_sha256=hashlib.sha256(cn.encode()).hexdigest(),
            san_dns=[cn], issuer_cn="Imported", trust_store=TrustStore.USER,
        )
        cert.serial = ":".join(cert.serial[i:i+2] for i in range(0, 12, 2))
        self._cert_counter += 1
        self.certificates.append(cert)
        self.events.insert(0, CertEvent(now, "imported", cn, "Certificate imported"))
        return cert

    # ─── Queries ───────────────────────────────────────────────────────

    def get_expiring_soon(self, days: int = 30) -> List[Certificate]:
        return [c for c in self.certificates
                if c.status != CertStatus.EXPIRED and 0 < c.days_until_expiry <= days]

    def get_expired(self) -> List[Certificate]:
        return [c for c in self.certificates if c.status == CertStatus.EXPIRED]

    def get_valid(self) -> List[Certificate]:
        return [c for c in self.certificates if c.status == CertStatus.VALID]

    def get_revoked(self) -> List[Certificate]:
        return [c for c in self.certificates if c.status == CertStatus.REVOKED]

    def search(self, query: str) -> List[Certificate]:
        q = query.lower()
        return [c for c in self.certificates
                if q in c.common_name.lower() or q in c.organization.lower()
                or any(q in d.lower() for d in c.san_dns)]

    def get_stats(self) -> Dict:
        return {
            "total": len(self.certificates),
            "valid": len(self.get_valid()),
            "expired": len(self.get_expired()),
            "expiring_soon": len(self.get_expiring_soon()),
            "revoked": len(self.get_revoked()),
            "chains": len(self.chains),
            "csrs": len(self.csrs),
            "events": len(self.events),
        }
