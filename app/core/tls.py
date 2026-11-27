from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
from pathlib import Path
from typing import Tuple

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def _cert_matches_sans(cert: x509.Certificate, lan_ip: str, min_valid_after: datetime) -> bool:
    if cert.not_valid_after_utc <= min_valid_after:
        return False
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return False

    dns_names = set(san.get_values_for_type(x509.DNSName))
    ip_addrs = set(san.get_values_for_type(x509.IPAddress))

    if "localhost" not in dns_names or ip_address("127.0.0.1") not in ip_addrs:
        return False

    clean_ip = (lan_ip or "").strip()
    if clean_ip:
        try:
            parsed = ip_address(clean_ip)
            if parsed not in ip_addrs:
                return False
        except ValueError:
            if clean_ip not in dns_names:
                return False
    return True


def ensure_lan_tls_cert(certs_dir: Path, lan_ip: str) -> Tuple[Path, Path]:
    """Ensure a valid self-signed LAN TLS certificate and key exist for mobile HTTPS camera access."""
    certs_dir.mkdir(parents=True, exist_ok=True)
    cert_path = certs_dir / "wims-lan.crt"
    key_path = certs_dir / "wims-lan.key"

    now = datetime.now(timezone.utc)
    if cert_path.exists() and key_path.exists():
        try:
            existing_cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
            key_bytes = key_path.read_bytes()
            serialization.load_pem_private_key(key_bytes, password=None)
            if _cert_matches_sans(existing_cert, lan_ip, now + timedelta(days=30)):
                return cert_path, key_path
        except Exception:
            pass

    private_key = ec.generate_private_key(ec.SECP256R1())
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "WhereIsMyStuff Local LAN"),
    ])

    san_entries: list[x509.GeneralName] = [
        x509.DNSName("localhost"),
        x509.IPAddress(ip_address("127.0.0.1")),
    ]
    clean_ip = (lan_ip or "").strip()
    if clean_ip:
        try:
            parsed = ip_address(clean_ip)
            if parsed != ip_address("127.0.0.1"):
                san_entries.append(x509.IPAddress(parsed))
        except ValueError:
            if clean_ip != "localhost":
                san_entries.append(x509.DNSName(clean_ip))

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=364))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .sign(private_key, hashes.SHA256())
    )

    key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)

    key_path.write_bytes(key_pem)
    cert_path.write_bytes(cert_pem)
    return cert_path, key_path
