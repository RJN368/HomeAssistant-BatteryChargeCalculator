"""TLS certificate management.

On first start, generates a self-signed certificate valid for 10 years and
prints its SHA-256 fingerprint to stdout.  Subsequent starts reuse the
existing cert/key files.

The user copies the printed fingerprint into Home Assistant to enable
certificate pinning (bypasses the self-signed CA warning safely).
"""

from __future__ import annotations

import datetime
import hashlib
import os

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.config import settings

_CERT_DIR = os.path.join(settings.data_dir, "certs")
_CERT_PATH = os.path.join(_CERT_DIR, "server.crt")
_KEY_PATH = os.path.join(_CERT_DIR, "server.key")


def _fingerprint(cert_path: str) -> str:
    """Return colon-delimited SHA-256 fingerprint of a PEM certificate."""
    with open(cert_path, "rb") as fh:
        cert = x509.load_pem_x509_certificate(fh.read())
    digest = hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()
    return ":".join(digest[i : i + 2] for i in range(0, len(digest), 2)).upper()


def ensure_certs() -> tuple[str, str]:
    """Return (cert_path, key_path), generating them on first run."""
    os.makedirs(_CERT_DIR, exist_ok=True)

    if os.path.exists(_CERT_PATH) and os.path.exists(_KEY_PATH):
        fp = _fingerprint(_CERT_PATH)
        print(  # noqa: T201
            f"\n[BCC ML Service] TLS certificate SHA-256 fingerprint: {fp}\n"
        )
        return _CERT_PATH, _KEY_PATH

    # Generate RSA 2048 private key
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )

    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "bcc-ml-service")]
    )

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(
            datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3650)
        )
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]),
            critical=False,
        )
        .sign(private_key, hashes.SHA256())
    )

    with open(_KEY_PATH, "wb") as fh:
        fh.write(
            private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )

    with open(_CERT_PATH, "wb") as fh:
        fh.write(cert.public_bytes(serialization.Encoding.PEM))

    fp = _fingerprint(_CERT_PATH)
    print(  # noqa: T201
        f"\n[BCC ML Service] Generated self-signed TLS certificate.\n"
        f"SHA-256 fingerprint: {fp}\n"
        "Copy this into Home Assistant → Integrations → Battery Charge Calculator "
        "→ Configure → ML Service TLS Fingerprint\n"
    )
    return _CERT_PATH, _KEY_PATH
