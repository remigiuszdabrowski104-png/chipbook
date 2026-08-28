# -*- coding: ascii -*-
"""TLS certificates that let a phone trust this laptop.

WHY THIS EXISTS. A browser only keeps a page available offline - as an
installed web app - when it was served over HTTPS. Without that, a phone
standing at the machine cannot start a job entry while the laptop is off.
Measured both ways: over plain HTTP the phone does not retain the page,
over HTTPS it does and opens it with the laptop shut down.

WHY A LOCAL CA AND NOT A BARE SELF-SIGNED CERTIFICATE.
A server certificate is issued for an ADDRESS, and a home router may hand
the laptop a different one tomorrow. If the phone trusted the certificate
itself, every address change would mean installing a new one by hand:

    local CA (created once)  ->  the phone trusts THIS, once, ever
      |
      +-- certificate for 192.168.1.19    (today)
      +-- certificate for 192.168.1.42    (after the router changes it -
                                           reissued automatically, the
                                           phone never notices)

WHAT REACHES THE PHONE: only `ca.crt`, the public half. The CA private key
never leaves the laptop and there is no reason it ever should.

LIFETIMES. Server certificates live 397 days because iOS rejects anything
longer than 398. The CA lives ten years, because replacing it means
walking over to the phone and installing trust again.

WHAT THIS MODULE DOES ON ITS OWN: nothing. A certificate is created only
when something asks for one, and only when the existing one no longer fits.
"""

import datetime
import ipaddress
import os

CERT_DIR = "certificates"

CA_CERT_FILE = "ca.crt"                 # this is what gets installed on a phone
CA_KEY_FILE = "ca-key.pem"              # this never leaves the laptop
SERVER_CERT_FILE = "server.crt"
SERVER_KEY_FILE = "server-key.pem"

CA_VALID_DAYS = 3650                    # ten years
SERVER_VALID_DAYS = 397                 # iOS rejects anything over 398
RENEW_MARGIN_DAYS = 30                  # reissue this long before expiry

MISSING_LIBRARY_MESSAGE = (
    "The 'cryptography' library is missing. Without it no certificate can "
    "be issued, and a phone will not reach the catalogue while the laptop "
    "is off.\n"
    "To fix it, open a command prompt and run:\n"
    "    python -m pip install cryptography\n"
    "This does not require administrator rights."
)


class MissingDependency(Exception):
    """Raised instead of a bare ImportError so the message can be shown to
    a person. `ModuleNotFoundError` means nothing to a machinist."""


def _crypto():
    """Import the crypto library lazily, only when it is actually needed.

    NOT AT MODULE LEVEL ON PURPOSE: the program must start for someone who
    does not have the library installed. With networking switched off,
    everything else works exactly as before. A missing dependency should
    disable ONE feature, not the whole application.
    """
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
    except ImportError:
        raise MissingDependency(MISSING_LIBRARY_MESSAGE)
    return x509, NameOID, ExtendedKeyUsageOID, hashes, serialization, rsa


def cert_dir(data_dir):
    """Certificates live WITH THE DATA, not with the program.

    Same reason as the database: updating the program replaces files in
    its own directory and must never touch anything that belongs to the
    user. A CA stored next to the code would be wiped by every update,
    and with it the phone's trust.
    """
    path = os.path.join(data_dir, CERT_DIR)
    os.makedirs(path, exist_ok=True)
    return path


def _paths(data_dir):
    directory = cert_dir(data_dir)
    return {
        "ca_cert": os.path.join(directory, CA_CERT_FILE),
        "ca_key": os.path.join(directory, CA_KEY_FILE),
        "server_cert": os.path.join(directory, SERVER_CERT_FILE),
        "server_key": os.path.join(directory, SERVER_KEY_FILE),
    }


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _save(path, payload):
    """Write to a temporary file, then rename over the target.

    If power is lost halfway through writing the CA key, a plain write
    would leave a stump that cannot be used - and the phone would stop
    trusting the laptop. A rename over a finished file is atomic.
    """
    temporary = path + ".new"
    with open(temporary, "wb") as file:
        file.write(payload)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)


def _load_cert(path):
    x509, _, _, _, _, _ = _crypto()
    with open(path, "rb") as file:
        return x509.load_pem_x509_certificate(file.read())


def _load_key(path):
    _, _, _, _, serialization, _ = _crypto()
    with open(path, "rb") as file:
        return serialization.load_pem_private_key(file.read(), password=None)


def authority(data_dir):
    """Create the local CA if it does not exist yet. Returns its paths.

    CREATED ONCE. Later calls leave an existing CA alone, because
    replacing it invalidates the trust already installed on every phone.
    """
    x509, NameOID, _, hashes, serialization, rsa = _crypto()
    paths = _paths(data_dir)
    if os.path.exists(paths["ca_cert"]) and os.path.exists(paths["ca_key"]):
        return paths["ca_cert"], paths["ca_key"]

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "chipbook - this laptop"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "chipbook"),
    ])
    now = _now()
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=CA_VALID_DAYS))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0),
                           critical=True)
            .add_extension(x509.KeyUsage(
                digital_signature=True, key_cert_sign=True, crl_sign=True,
                key_encipherment=False, content_commitment=False,
                data_encipherment=False, key_agreement=False,
                encipher_only=False, decipher_only=False), critical=True)
            .sign(key, hashes.SHA256()))

    _save(paths["ca_key"], key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()))
    _save(paths["ca_cert"],
          cert.public_bytes(serialization.Encoding.PEM))
    return paths["ca_cert"], paths["ca_key"]


def for_install(data_dir):
    """Path to the single file that gets installed on the phone."""
    return _paths(data_dir)["ca_cert"]


def _covers(cert, addresses):
    """Does the existing certificate already name every address we need?"""
    x509, _, _, _, _, _ = _crypto()
    try:
        extension = cert.extensions.get_extension_for_class(
            x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return False
    known = set(str(a) for a in extension.get_values_for_type(x509.IPAddress))
    known |= set(extension.get_values_for_type(x509.DNSName))
    return set(addresses) <= known


def _still_valid(cert):
    """Does the certificate survive the next month?"""
    end = cert.not_valid_after_utc
    return end - _now() > datetime.timedelta(days=RENEW_MARGIN_DAYS)


def for_hosts(data_dir, addresses):
    """Return (cert, key) covering the given addresses of this laptop.

    A new certificate is issued only when the current one is missing,
    expires within a month, or does NOT name one of the given addresses -
    which is exactly the case where the router handed the laptop a new IP.
    The CA is untouched, so the phone notices nothing.
    """
    x509, NameOID, ExtendedKeyUsageOID, hashes, serialization, rsa = _crypto()
    addresses = [a for a in addresses if a]
    if not addresses:
        raise ValueError("No address given.")
    paths = _paths(data_dir)
    authority(data_dir)

    if (os.path.exists(paths["server_cert"])
            and os.path.exists(paths["server_key"])):
        previous = _load_cert(paths["server_cert"])
        if _still_valid(previous) and _covers(previous, addresses):
            return paths["server_cert"], paths["server_key"]

    ca_cert = _load_cert(paths["ca_cert"])
    ca_key = _load_key(paths["ca_key"])

    names = []
    for address in addresses:
        try:
            names.append(x509.IPAddress(ipaddress.ip_address(address)))
        except ValueError:
            names.append(x509.DNSName(address))

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = _now()
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([
                x509.NameAttribute(NameOID.COMMON_NAME, addresses[0])]))
            .issuer_name(ca_cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=SERVER_VALID_DAYS))
            .add_extension(x509.SubjectAlternativeName(names), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None),
                           critical=True)
            .add_extension(x509.ExtendedKeyUsage(
                [ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(ca_key, hashes.SHA256()))

    _save(paths["server_key"], key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()))
    _save(paths["server_cert"], cert.public_bytes(serialization.Encoding.PEM))
    return paths["server_cert"], paths["server_key"]


def library_present():
    """Whether certificates can be issued at all - shown in the UI."""
    try:
        _crypto()
        return True
    except MissingDependency:
        return False
