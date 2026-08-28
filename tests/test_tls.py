"""Tests for certificate issuing (tls.py).

WHAT THESE TESTS PROTECT. A phone's trust in this laptop is installed ONCE,
by hand, standing at the phone. Everything that could silently invalidate
it has a test here:

  - the CA is created once and does NOT change on later runs;
  - when the laptop's address changes, a new server certificate is issued
    but the CA stays THE SAME - otherwise trust would have to be installed
    again on every phone after every address change;
  - a server certificate never lives longer than 398 days, because iOS
    rejects those outright;
  - the CA private key never reaches the file that goes to the phone.

None of these tests needs a network or a running server.
"""

import os
import shutil
import tempfile
import unittest

from chipbook.server import tls

# THE PHONE ROAD IS AN OPTIONAL EXTRA (pip install -e .[phone]). Without
# the library there is nothing here to measure, so these tests step aside
# and SAY SO - the same way the setup-sheet tests do when the real file is
# not on the disk. A missing extra is not a failure; a silent pass would be.
NEEDS_LIBRARY = unittest.skipUnless(
    tls.library_present(),
    '"cryptography" is missing - install the extra: pip install -e .[phone]')


def _bytes_of(path):
    with open(path, "rb") as file:
        return file.read()


def _cert(path):
    from cryptography import x509
    return x509.load_pem_x509_certificate(_bytes_of(path))


@NEEDS_LIBRARY
class CertificateAuthorityTest(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="chipbook-cert-")

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_ca_is_created_with_both_files(self):
        cert, key = tls.authority(self.directory)
        self.assertTrue(os.path.exists(cert))
        self.assertTrue(os.path.exists(key))

    def test_ca_is_created_ONLY_ONCE(self):
        """If it were recreated on every start, every phone would lose its
        trust each time the program launched."""
        cert, _ = tls.authority(self.directory)
        first = _bytes_of(cert)
        tls.authority(self.directory)
        tls.authority(self.directory)
        self.assertEqual(first, _bytes_of(cert))

    def test_ca_lives_with_the_data_not_with_the_program(self):
        """Updating the program replaces files in its own directory. A CA
        stored there would be wiped by every update, taking the phone's
        trust with it."""
        cert, _ = tls.authority(self.directory)
        self.assertTrue(os.path.abspath(cert).startswith(
            os.path.abspath(self.directory)))

    def test_ca_outlives_the_server_certificate(self):
        """Replacing the CA means walking to every phone. Replacing a
        server certificate costs nothing. The two cannot share a
        lifetime."""
        cert, _ = tls.authority(self.directory)
        server_cert, _ = tls.for_hosts(
            self.directory, ["192.168.1.19"])
        self.assertGreater(_cert(cert).not_valid_after_utc,
                           _cert(server_cert).not_valid_after_utc)

    def test_installable_file_carries_no_private_key(self):
        """This is the only file that ever leaves the laptop. If the CA
        key leaked into it, anyone could impersonate this machine."""
        tls.authority(self.directory)
        path = tls.for_install(self.directory)
        content = _bytes_of(path)
        self.assertIn(b"BEGIN CERTIFICATE", content)
        self.assertNotIn(b"PRIVATE KEY", content)


@NEEDS_LIBRARY
class ServerCertificateTest(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="chipbook-cert-")

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def _san(self, cert_path):
        from cryptography import x509
        return _cert(cert_path).extensions.get_extension_for_class(
            x509.SubjectAlternativeName).value

    def test_certificate_names_the_given_address(self):
        from cryptography import x509
        cert, _ = tls.for_hosts(self.directory, ["192.168.1.19"])
        addresses = [str(a) for a
                     in self._san(cert).get_values_for_type(x509.IPAddress)]
        self.assertIn("192.168.1.19", addresses)

    def test_not_longer_than_398_days(self):
        """iOS rejects server certificates valid for more than 398 days.
        If someone raised this "to renew less often", phones would stop
        connecting and the symptom would not point at the cause."""
        cert, _ = tls.for_hosts(self.directory, ["192.168.1.19"])
        issued = _cert(cert)
        days = (issued.not_valid_after_utc
                - issued.not_valid_before_utc).days
        self.assertLessEqual(days, 398)

    def test_same_address_does_not_reissue(self):
        """A fresh certificate on every start would drop the phone's
        connections for no reason."""
        cert, _ = tls.for_hosts(self.directory, ["192.168.1.19"])
        first = _bytes_of(cert)
        tls.for_hosts(self.directory, ["192.168.1.19"])
        self.assertEqual(first, _bytes_of(cert))

    def test_ADDRESS_CHANGE_reissues_but_keeps_the_CA(self):
        """THE MOST IMPORTANT TEST IN THIS FILE.

        The router hands the laptop a different address. The server
        certificate MUST change, because the old one no longer matches -
        but the CA MUST stay the same, or trust has to be installed on
        every phone again. The entire value of the local-CA design sits
        in this one assertion.
        """
        ca_cert = tls.for_install(self.directory)
        first, _ = tls.for_hosts(self.directory, ["192.168.1.19"])
        old_server = _bytes_of(first)
        old_ca = _bytes_of(ca_cert)

        second, _ = tls.for_hosts(self.directory, ["192.168.1.42"])

        self.assertNotEqual(old_server, _bytes_of(second))
        self.assertEqual(old_ca, _bytes_of(ca_cert))

    def test_reissued_certificate_is_signed_by_THE_SAME_CA(self):
        """It is not enough that the CA file did not change - the new
        certificate has to actually descend from it, or the phone rejects
        it despite the installed trust."""
        tls.for_hosts(self.directory, ["192.168.1.19"])
        second, _ = tls.for_hosts(self.directory, ["10.0.0.7"])
        authority = _cert(tls.for_install(self.directory))
        self.assertEqual(_cert(second).issuer, authority.subject)

    def test_several_addresses_at_once(self):
        """A laptop often has more than one interface - Wi-Fi and cable."""
        from cryptography import x509
        cert, _ = tls.for_hosts(
            self.directory, ["192.168.1.19", "10.0.0.7"])
        addresses = [str(a) for a
                     in self._san(cert).get_values_for_type(x509.IPAddress)]
        self.assertIn("192.168.1.19", addresses)
        self.assertIn("10.0.0.7", addresses)

    def test_a_hostname_is_accepted_too(self):
        """Not every address is a number. A hostname has to be recorded as
        a DNS name rather than break the issuing."""
        from cryptography import x509
        cert, _ = tls.for_hosts(self.directory, ["shop-laptop"])
        self.assertIn("shop-laptop",
                      self._san(cert).get_values_for_type(x509.DNSName))

    def test_no_address_raises_instead_of_issuing(self):
        """A certificate for nothing is worse than no certificate: it
        would look like it works and fail only on the phone."""
        with self.assertRaises(ValueError):
            tls.for_hosts(self.directory, [])
        with self.assertRaises(ValueError):
            tls.for_hosts(self.directory, [None, ""])

    def test_server_key_is_not_the_CA_key(self):
        """If the server used the CA key, a leak from the server would be
        a leak of the entire trust chain."""
        _, server_key = tls.for_hosts(
            self.directory, ["192.168.1.19"])
        _, ca_key = tls.authority(self.directory)
        self.assertNotEqual(_bytes_of(server_key), _bytes_of(ca_key))


class MissingLibraryTest(unittest.TestCase):

    def test_message_says_what_to_do_not_what_broke(self):
        """This text is read by someone who is not a programmer. It has to
        contain a command they can retype, not the name of an exception."""
        self.assertIn("pip install cryptography",
                      tls.MISSING_LIBRARY_MESSAGE)
        self.assertIn("administrator", tls.MISSING_LIBRARY_MESSAGE)
        self.assertNotIn("ImportError", tls.MISSING_LIBRARY_MESSAGE)

    def test_library_present_answers_without_raising(self):
        self.assertIn(tls.library_present(), (True, False))


if __name__ == "__main__":
    unittest.main()
