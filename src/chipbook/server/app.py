"""Standing the server up and keeping it standing.

WHAT THIS FILE DECIDES: which port, whether the phone is let in at all,
what happens when the window is closed, and what the person sees in the
console. The answers to requests are next door, in routes.py.

THE PROGRAM LISTENS ON 127.0.0.1 BY DEFAULT - that is, on this computer
only. Nothing goes out to the network and nobody from outside can connect
until the owner turns the phone on deliberately.
"""

import os
import secrets
import socket
import ssl
import sys
import threading
import urllib.request
import webbrowser
from http.server import HTTPServer

from .. import catalog
from . import tls
from .routes import RequestHandler, file_stamp, phone_code


# Default place for the data. DELIBERATELY outside the program directory:
# the program can be deleted and set up again, the data must survive it.
DEFAULT_DATA_DIR = os.path.join(os.path.expanduser("~"), "chipbook-data")


# THE PORT IS FIXED, AND THAT IS A DECISION, NOT A DETAIL.
# The program used to look for the first free port in the range
# 8756-8776. On a single computer that did no harm, but for the phone the
# address is something a person types by hand - and it changed after every
# restart. Reported from use: "best if the port were fixed and did not
# change, because it is nothing but trouble".
# A WORSE THING GOES AWAY WITH IT: scanning a range let a SECOND copy of
# the program come up on a neighbouring port and work on the same
# database. Now a second start simply fails and says why.
DEFAULT_PORT = 8756


# How long the program waits after the window is closed before it ends.
#
# THIS IS NOT AN "idle timeout" and there is no watching for inactivity
# here. The program runs for as long as the window is open - even when
# nobody touches it for half a day. This clock starts ONLY when the
# browser says the window has been closed.
#
# These dozen seconds are here so that REFRESHING THE PAGE (F5) ends
# nothing: the browser sends the same goodbye then as on closing, but the
# new page speaks up at once and cancels the shutdown.
SHUTDOWN_GRACE_SECONDS = 12.0


class ChipbookServer(HTTPServer):
    """A server that does NOT let two copies sit on the same port.

    Python's default setting (SO_REUSEADDR) does not block a second program
    on Windows - both copies settle in without an error and requests from
    the browser reach now one, now the other. Measured on Windows 11: a test
    expecting an error passed on Linux and failed on Windows. Hence
    SO_REUSEADDR is switched off explicitly and SO_EXCLUSIVEADDRUSE is
    switched on where it exists (Windows).
    """

    allow_reuse_address = False
    shutdown_timer = None

    # "http" or "https". Serves to check whether a request came from OUR own
    # page - see _same_origin. This used to be a hard-coded "http", and then
    # every request from the phone through the second, secured entrance would
    # be rejected as foreign.
    scheme = "http"

    # At the entrance for the phone this points at the main server. The
    # shutdown clock is ONE and belongs to the main server - otherwise the
    # phone would cancel its own clock and the program would close anyway.
    primary = None

    def schedule_shutdown(self, after=None):
        """The window has been closed - we end in a moment.

        NOT at once, because the browser sends the same goodbye on an ORDINARY
        REFRESH of the page. A dozen seconds is enough for the new page to
        speak up and for the shutdown to be cancelled.
        """
        if self.primary is not None:
            return self.primary.schedule_shutdown(after)
        self.cancel_shutdown()
        self.shutdown_timer = threading.Timer(
            SHUTDOWN_GRACE_SECONDS if after is None else after, self.shutdown)
        self.shutdown_timer.daemon = True
        self.shutdown_timer.start()

    def cancel_shutdown(self):
        """Anything at all from the window means the window is alive."""
        if self.primary is not None:
            return self.primary.cancel_shutdown()
        if self.shutdown_timer is not None:
            self.shutdown_timer.cancel()
            self.shutdown_timer = None

    # THE CODE, THE TOKEN AND THE BAD-ATTEMPT COUNTER ARE SHARED BY BOTH
    # ENTRANCES. If each entrance had its own, clicking "New code" on the
    # laptop would change the code on the laptop side only and the phone would
    # still come in with the old one. The lock for the phone would stop
    # meaning anything.
    _token = None
    _code = None
    _bad_attempts = 0

    @property
    def token(self):
        return self.primary.token if self.primary is not None else self._token

    @token.setter
    def token(self, value):
        if self.primary is not None:
            self.primary.token = value
        else:
            self._token = value

    @property
    def code(self):
        return self.primary.code if self.primary is not None else self._code

    @code.setter
    def code(self, value):
        if self.primary is not None:
            self.primary.code = value
        else:
            self._code = value

    @property
    def failed_attempts(self):
        return (self.primary.failed_attempts if self.primary is not None
                else self._bad_attempts)

    @failed_attempts.setter
    def failed_attempts(self, value):
        if self.primary is not None:
            self.primary.failed_attempts = value
        else:
            self._bad_attempts = value

    def server_bind(self):
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, exclusive, 1)
            except OSError:
                pass  # older Windows - allow_reuse_address alone remains
        HTTPServer.server_bind(self)


# THE OLD POLISH NAMES ARE STILL HONOURED, and that is not sentiment.
# Silently ignoring a setting somebody already has would open the program
# on a DIFFERENT database and show an empty catalogue - which looks exactly
# like lost data. The English name wins when both are set.
_OLD_NAMES = {"CHIPBOOK_DATA": "CHIPBOOK_DANE",
              "CHIPBOOK_NETWORK": "CHIPBOOK_SIEC"}


def _from_env(name, old_name=""):
    """The setting under its English name, falling back to the old one."""
    value = os.environ.get(name, "").strip()
    if value:
        return value
    return os.environ.get(old_name or _OLD_NAMES.get(name, ""), "").strip()


def network_enabled(setting=None):
    """Whether the program is to be visible to other devices on the network.

    OFF BY DEFAULT. Switched on explicitly, one line in the environment:
        set CHIPBOOK_NETWORK=1
    Without that everything stays as it was - the server listens only on
    this computer and no firewall has anything to ask about.
    """
    if setting is not None:
        return bool(setting)
    return _from_env("CHIPBOOK_NETWORK", "CHIPBOOK_SIEC").strip().lower() in (
        "1", "true", "yes", "on")


def _home_address(address):
    """Whether this is an address from a home or company network, not the internet."""
    if address.startswith("192.168.") or address.startswith("10."):
        return True
    if address.startswith("172."):
        try:
            second = int(address.split(".")[1])
        except (IndexError, ValueError):
            return False
        return 16 <= second <= 31
    return False


def network_address():
    """The address of this computer as seen by a phone on the same network.

    TWO ROADS, AND THE SECOND EXISTS FOR A CONCRETE REASON. The user's
    laptop stands at work WITH NO INTERNET - the phone is to see it over the
    local network. The first road asks the system which card it would use to
    go outside; it is the most reliable, but on a network with no road
    outside it can return NOTHING. The program would then say "I do not know
    my own address", even though the phone is standing right next to it on
    the same Wi-Fi.
    So the second road asks for the addresses assigned to the computer
    itself and takes the first one from a home network
    (192.168 / 10 / 172.16-31).
    The socket in the first road is UDP - it sends nothing, it serves only
    to pick a route.
    When neither road gives anything (a computer with no network), we return
    None instead of inventing an address that would not work anyway.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.168.1.1", 9))
        address = sock.getsockname()[0]
        if address and not address.startswith("127."):
            return address
    except OSError:
        pass
    finally:
        sock.close()

    try:
        results = socket.getaddrinfo(socket.gethostname(), None,
                                    socket.AF_INET)
    except OSError:
        return None
    addresses = [w[4][0] for w in results]
    for address in addresses:
        if _home_address(address):
            return address
    for address in addresses:
        if not address.startswith("127."):
            return address
    return None


def port_from_settings():
    """The port number. Fixed by default; changeable in the environment.

    The change is foreseen for one case: when 8756 is taken on somebody's
    computer by another program. Then `set CHIPBOOK_PORT=8790` and the
    address for the phone changes once, not at every start.
    An entry that cannot be read as a sensible port is IGNORED - a typo is
    to leave the program running, not to lay it flat with an error message.
    """
    text = os.environ.get("CHIPBOOK_PORT", "").strip()
    if text.isdigit() and 1024 <= int(text) <= 65535:
        return int(text)
    return DEFAULT_PORT


def build(data_dir=None, port_from=None, port_to=None, network=None):
    """Builds a ready server (not listening yet). Used by the tests too."""
    data_dir = (data_dir or _from_env("CHIPBOOK_DATA", "CHIPBOOK_DANE")
                      or DEFAULT_DATA_DIR)
    if port_from is None:
        port_from = port_from_settings()
    if port_to is None:
        port_to = port_from
    store = catalog.open_catalog(data_dir)
    on_network = network_enabled(network)
    # THE PHONE COMES IN BY A SEPARATE, SECURED ENTRANCE.
    # As long as a certificate can be issued, the ordinary entrance stays
    # EXCLUSIVELY on this computer - the phone does not need it anyway, and
    # over an ordinary connection it would not remember the page, so the whole
    # point of working with the laptop shut could not work.
    # Without the library everything stays as it was before: one entrance,
    # ordinary, visible on the network. The program has to come up then too.
    phone_over_https = on_network and tls.library_present()
    listen_address = "0.0.0.0" if (on_network and not phone_over_https) else "127.0.0.1"

    server = None
    for port in range(port_from, port_to + 1):
        try:
            server = ChipbookServer((listen_address, port), RequestHandler)
            break
        except OSError:
            continue
    if server is None:
        store.close()
        if port_from == port_to:
            raise catalog.ChipbookError(
                "Port %d is taken. Most often that means chipbook IS "
                "ALREADY RUNNING - look for its window in the browser. If "
                "it certainly is not, another program took the port; then "
                "set CHIPBOOK_PORT=8790 and start it again"
                % port_from)
        raise catalog.ChipbookError(
            "No port from %d to %d is free. Is chipbook not running "
            "in another window already?" % (port_from, port_to))

    port = server.server_address[1]
    server.catalog = store
    server.token = secrets.token_urlsafe(24)
    server.allowed_origins = {"http://127.0.0.1:%d" % port,
                        "http://localhost:%d" % port}
    server.address = "http://127.0.0.1:%d/" % port
    server.network = on_network
    # The code exists ONLY with the network switched on. Without it nobody
    # from outside will connect anyway, so the file with the code has no
    # reason to come into being.
    server.code = phone_code(data_dir) if on_network else None
    server.failed_attempts = 0
    # The address for the phone. Computed ONLY with the option switched on -
    # so that a program running normally does not look at the network even
    # for that.
    server.phone_address = None
    # The addresses under which THIS computer is itself. The loopback always,
    # and with the network switched on also our own address on the network -
    # see _is_local.
    server.my_addresses = {"127.0.0.1", "::1"}
    # SET BEFORE the entrance for the phone, because that one copies it.
    server.stamp = file_stamp()
    server.scheme = "http"
    server.phone = None
    server.phone_problem = None
    server.phone_certificate = None
    if on_network:
        my_address = network_address()
        if my_address:
            server.my_addresses.add(my_address)
            if phone_over_https:
                server.phone, server.phone_problem = (
                    phone_entry(server, data_dir, my_address, port))
                if server.phone is not None:
                    server.phone_address = server.phone.phone_address
                    server.phone_certificate = (
                        tls.for_install(data_dir))
            else:
                server.phone_address = "http://%s:%d/" % (my_address, port)
                server.phone_problem = tls.MISSING_LIBRARY_MESSAGE
    return server

PHONE_PORT_TRIES = 10


def phone_ports(port):
    """The port numbers the phone may come in by - nearest first.

    WHY NOT THE SAME ONE AS THE WINDOW: one port cannot speak the ordinary
    and the secured language at the same time.
    WHY NEXT DOOR: the user has to copy this address from the window into
    the phone - a number one higher can be copied, a random one cannot.
    WHY MORE THAN ONE: MEASURED IN USE - the port one higher WAS TAKEN by
    another program and the entrance for the phone did not come up at all.
    The symptom would read "the phone does not work", and the cause would
    have nothing to do with the phone. So we look for the next free one,
    still close to the window.
    """
    return [port + step for step in range(1, PHONE_PORT_TRIES + 1)]


def phone_port(port):
    """The first number off the top. Kept for readability at call sites."""
    return phone_ports(port)[0]


def phone_entry(primary, data_dir, my_address, main_port):
    """A second entrance to the same database - secured, for the phone.

    Returns (server, trouble). On failure the server is None and trouble
    carries a sentence to show a person. It NEVER knocks the program over:
    a missing entrance for the phone is to take away the phone, not chipbook
    on the laptop.
    """
    candidates = phone_ports(main_port)
    try:
        cert, key = tls.for_hosts(
            data_dir, [my_address, "127.0.0.1", socket.gethostname()])
        settings = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        settings.load_cert_chain(cert, key)
    except tls.MissingDependency as error:
        return None, str(error)
    except Exception as error:                          # noqa: BLE001
        return None, ("The certificate for the phone could not be "
                      "prepared: %s" % error)

    second = None
    last_error = None
    for port in candidates:
        try:
            second = ChipbookServer(("0.0.0.0", port), RequestHandler)
            break
        except OSError as error:
            last_error = error
            continue
    if second is None:
        return None, ("No port from %d to %d is free for the entrance for "
                      "the phone (%s). The chipbook port can be changed "
                      "in the settings, and this one will follow it."
                      % (candidates[0], candidates[-1], last_error))
    port = second.server_address[1]

    second.socket = settings.wrap_socket(second.socket, server_side=True)
    second.primary = primary          # the code, the token and the shutdown clock are shared
    second.scheme = "https"
    second.catalog = primary.catalog
    second.network = True
    second.stamp = primary.stamp if hasattr(primary, "stamp") else None
    second.my_addresses = primary.my_addresses
    second.address = primary.address
    second.phone = None
    second.phone_problem = None
    second.phone_certificate = None
    second.phone_address = "https://%s:%d/" % (my_address, port)
    second.allowed_origins = {second.phone_address.rstrip("/")}
    return second, None


def notify(content):
    """A message that is seen ALSO when there is no black window.

    Started from a shortcut rather than a console - `pythonw`, or a
    double-clicked launcher somebody made for themselves - the program has
    no console, and `print` has nobody to tell anything to. Without this
    every failure to start would be MUTE: a click, and nothing happens.
    There will be nobody around the end user to look deeper.
    The little window is drawn by Windows itself through ctypes - no library
    from outside, the same road as the Recycle Bin.
    """
    print(content)
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, content, "chipbook", 0x40)
    except Exception:                                  # noqa: BLE001
        pass       # not Windows, or no desktop - print alone remains


def already_running(port):
    """Whether OUR program answers on this port, and not somebody else's.

    We ask for the icon, because that is the only address served WITHOUT a
    token (and a second copy's token would be different anyway). A foreign
    program on this port will not answer with an image.
    """
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/favicon.ico" % port, timeout=2) as answer:
            return answer.headers.get("Content-Type") == "image/x-icon"
    except Exception:                                  # noqa: BLE001
        return False


def main(arguments=None):
    arguments = list(sys.argv[1:] if arguments is None else arguments)

    # A CATALOGUE TO LOOK AT BEFORE ANYTHING HAS BEEN TYPED.
    # Started plain, chipbook shows an empty list - right for the person it
    # was built for, useless for somebody who is only having a look. This
    # switch fills a catalogue OF ITS OWN with invented jobs, so the search
    # and the setup-sheet reader can be seen working in the first minute.
    # It never writes into the real data directory: see chipbook/demo.py.
    demo = "--demo" in arguments
    arguments = [a for a in arguments if a != "--demo"]
    directory = arguments[0] if arguments else None

    if demo:
        from .. import demo as demo_module
        try:
            directory, added = demo_module.fill(directory)
        except catalog.ChipbookError as error:
            notify("The demo catalogue could not be made.\n\n" + str(error))
            return 1
        print("DEMO CATALOGUE - the jobs in it are invented.")
        print("  %s" % ("%d jobs written." % added if added
                        else "Already there, nothing added."))
        print("  It lives in a directory of its own; your own catalogue is")
        print("  untouched. Delete the directory below and it is gone.")
        print("")

    try:
        server = build(directory)
    except catalog.ChipbookError as error:
        # CLICKING THE ICON WITH THE PROGRAM ALREADY RUNNING MUST OPEN ITS
        # WINDOW, NOT DO NOTHING. That is how every normal program behaves.
        # Caught in use: chipbook was running in the background, clicking the
        # icon tried to bring up a second copy, the port was taken and the copy
        # died quietly - with no console there was no way to see it, so it looked
        # as though the program was broken.
        port = port_from_settings()
        if already_running(port):
            webbrowser.open("http://127.0.0.1:%d/" % port)
            return 0
        notify("chipbook did not start.\n\n" + str(error))
        return 1

    print("chipbook is running.")
    print("  data:    " + server.catalog.data_dir)
    print("  address: " + server.address)
    print("  jobs in the database: %d" % server.catalog.job_count())
    if server.network:
        print("")
        print("  VISIBLE ON THE NETWORK (CHIPBOOK_NETWORK=1).")
        if server.phone_problem:
            print("")
            print("  CAREFUL - the phone will come in only over an ordinary")
            print("  connection, which means it will NOT remember the")
            print("  page and will not work with the laptop shut:")
            for line in str(server.phone_problem).splitlines():
                print("    " + line)
            print("")
        if server.phone_certificate:
            print("  the certificate to install on the phone:")
            print("    " + server.phone_certificate)
            print("  It is installed ONCE in the life of the phone.")
        if server.phone_address:
            print("  address for the phone: " + server.phone_address)
        else:
            print("  I DO NOT KNOW MY OWN ADDRESS ON THE NETWORK.")
            print("  The internet is not needed for anything, but the")
            print("  phone and this computer have to be on ONE network.")
            print("  Connect both to the same Wi-Fi, or switch on the")
            print("  hotspot on the phone and join the computer to it.")
        print("  The phone will ask for the code. The code is shown by the")
        print("  'New code' button in the window - clicking it draws a")
        print("  fresh one.")
    print("")
    print("The window should open by itself. To close the program:")
    print("come back to this window and press Ctrl+C.")
    try:
        webbrowser.open(server.address)
    except Exception:                                  # noqa: BLE001
        print("(the browser could not be opened - paste the address by hand)")
    # THE ENTRANCE FOR THE PHONE RUNS IN A SEPARATE THREAD. The thread is a
    # "daemon", so it will not hold the program up should anything go wrong at
    # closing time - the database is to close just as it did before.
    phone_thread = None
    if server.phone is not None:
        phone_thread = threading.Thread(
            target=server.phone.serve_forever, daemon=True)
        phone_thread.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("")
        print("Closing down. The data is saved.")
    finally:
        if server.phone is not None:
            try:
                server.phone.shutdown()
                server.phone.server_close()
            except Exception:                          # noqa: BLE001
                pass    # closing has no right to hide the real error
        server.server_close()
        server.catalog.close()
    return 0
if __name__ == "__main__":
    sys.exit(main())
