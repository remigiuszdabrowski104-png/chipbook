"""What a browser request turns into.

ONE HANDLER, ONE ADDRESS TABLE. Everything the window can ask for is here,
and nothing here knows how the server was started or on which port - that
belongs next door, in app.py.

TWO RULES RUN THROUGH THE WHOLE FILE:

    A REQUEST FROM OUTSIDE THIS COMPUTER GETS LESS. The phone may ask a
    question, add a job and send a file; it may not open a file on the
    laptop or shut the program down.

    THE PAGE IS SERVED WITH ITS OWN TOKEN INSIDE IT. A page served beyond
    this computer gets none - knowing the address used to be enough, and
    that was a hole.
"""

import hashlib
import json
import os
import secrets
import struct
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, quote, unquote

from .. import ai
from .. import catalog
from .. import setupsheet


PACKAGE_DIR = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))

UI_FILE = os.path.join(PACKAGE_DIR, "web", "index.html")
STYLES_FILE = os.path.join(PACKAGE_DIR, "web", "styles.css")
SCRIPT_FILE = os.path.join(PACKAGE_DIR, "web", "app.js")

STYLES_PLACEHOLDER = "__STYLES__"
SCRIPT_PLACEHOLDER = "__SCRIPT__"


def page_source():
    """The whole page as one piece: the markup with the look and the code
    already inside it.

    THE PAGE LIES IN THREE FILES AND IS SERVED AS ONE, and both halves of
    that sentence are deliberate. Three files, because three thousand lines
    in a single one are not readable by anybody. One page, because the
    phone keeps a copy of it for when the laptop is off - and a copy
    assembled from three separate fetches is three chances to end up
    holding halves from two different versions.
    """
    with open(UI_FILE, encoding="utf-8") as file:
        html = file.read()
    with open(STYLES_FILE, encoding="utf-8") as file:
        html = html.replace(STYLES_PLACEHOLDER, file.read())
    with open(SCRIPT_FILE, encoding="utf-8") as file:
        html = html.replace(SCRIPT_PLACEHOLDER, file.read())
    return html

ICON_FILE = os.path.join(PACKAGE_DIR, "web", "chipbook.ico")

TOKEN_PLACEHOLDER = "__TOKEN__"


# The spot in web/index.html where the home-screen icon for the phone goes.
ICON_PLACEHOLDER = "__ICON__"


# The program version number written into the page. Visible even with the
# laptop shut - otherwise there is no telling whether a fix arrived.
VERSION_PLACEHOLDER = "__WINDOW_VERSION__"


# THE LOCK FOR THE PHONE. Anyone who knew the address used to get a ready
# token together with the page, and full access. Now the page served to
# ANYONE OUTSIDE THIS COMPUTER arrives without a token, and the phone must
# first give the six-digit code shown in the window on the laptop.
# THE CODE IS PERMANENT, THE TOKEN IS NOT. The code lies in the data
# directory, so the phone types it ONCE in its life and not after every
# start of the program; the token is still created anew at every start and
# the phone quietly trades its remembered code for it.
PHONE_CODE_FILE = "phone-code.txt"

MAX_CODE_ATTEMPTS = 10


# File types that may be shown IN THE BROWSER instead of being downloaded.
# THE LIST IS SHORT AND THAT IS DELIBERATE: a file served "to look at"
# runs at our own address, so HTML or SVG could run their own code inside
# it and reach into the database. These formats the browser only draws.
# Whatever is not here loads as before, that is as a download.
# FROM THE NETWORK ONLY WHAT IS ON THIS LIST IS ALLOWED. THE LIST CLOSES
# FROM THE TOP: what is not written down is forbidden - including things
# nobody thought of today and things somebody adds a year from now.
# WHY NOT "from the network only reading is allowed": because a question
# to the AI is a write (a POST carrying the question), and that is the
# main reason the user reaches for the phone at all. Splitting this into
# reading and writing would block exactly that.
# /api/jobs was added later. Until then the phone could create a job ONLY
# with the laptop shut (through the queue), and with the laptop running it
# could not at all - the opposite of what common sense suggests. Now, with
# the laptop running, the phone opens the ORDINARY chipbook form and saves
# the same way the window on the laptop does. The level of trust is the
# same as for adding photos: the phone had to give the code anyway.
REMOTE_ALLOWED = ("/api/ask", "/api/shutdown", "/api/jobs/offline",
                 "/api/jobs")

REMOTE_ALLOWED_SUFFIXES = ("/notes", "/files")


INLINE_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".txt": "text/plain; charset=utf-8",
}


# Files that Python reads ONCE, at startup. web/index.html is read on every page
# open, so it is not here - changing it needs no restart and there is no
# point in warning about it.
# web/index.html was added to this list later and that is not a detail.
# CAUGHT ON A PHONE: the number of the stored copy is computed from this
# list, and web/index.html was not on it. A change to the look of the window did
# NOT change the copy number - the phone kept serving itself the old page
# out of its own memory and two consecutive fixes never reached it. The
# symptom read "you fixed nothing", and the fixes were good.
CACHE_BUSTED_FILES = (
    "catalog.py",
    os.path.join("server", "__init__.py"),
    os.path.join("setupsheet", "__init__.py"),
    os.path.join("ai", "__init__.py"),
    os.path.join("web", "index.html"),
    os.path.join("web", "styles.css"),
    os.path.join("web", "app.js"),
)


# The file the browser keeps at its own end, thanks to which it opens
# chipbook with the laptop shut. Assembled in code, not kept as a separate
# file - one file less in the package for the user, the same principle as
# for the app description.
SW_JS = """/* chipbook - the copy of the page stored in the phone. Copy no. %(copy_number)s */
var STORE = "chipbook-%(copy_number)s";
var TO_STORE = ["/", "/manifest.json", "/icon.png", "/favicon.ico"];

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(STORE).then(function (store) {
      return store.addAll(TO_STORE);
    }).then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener("activate", function (event) {
  /* Copies with a different number come from an older version of the
     program and have to go, otherwise after an update the phone would
     show the previous window. */
  event.waitUntil(
    caches.keys().then(function (names) {
      return Promise.all(names.map(function (name) {
        return name === STORE ? null : caches.delete(name);
      }));
    }).then(function () { return self.clients.claim(); })
  );
});

self.addEventListener("fetch", function (event) {
  var address = new URL(event.request.url);
  /* The conversation with the database must NOT come from the stored copy. */
  if (address.pathname.indexOf("/api/") === 0) { return; }
  if (event.request.method !== "GET") { return; }

  /* OUR OWN MEMORY FIRST, THE NETWORK ONLY AFTER IT.
     MEASURED ON AN IPHONE: with the order reversed (the network first)
     the phone with the laptop shut ENDED ON A CONNECTION ERROR, even
     though it had the copies - the status box showed "remembered: YES,
     stored parts: 4", and Safari still said "the server stopped
     responding".
     A trial version was laid out exactly the way it is here and on THE
     SAME phone it opened with no laptop. We copy what was measured
     instead of inventing it a fifth time.
     THE PRICE, NAMED OUT LOUD: a page served from memory may be one run
     of the program out of date. It does not hurt, because the copy
     number comes from the file stamp - an update of the program sweeps
     the old copy out whole anyway. */
  event.respondWith(
    caches.match(event.request).then(function (stored) {
      if (stored) { return stored; }
      return fetch(event.request).then(function (answer) {
        if (answer && answer.ok) {
          var copy = answer.clone();
          caches.open(STORE).then(function (store) {
            store.put(event.request, copy);
          });
        }
        return answer;
      }).catch(function () {
        /* We serve the main page ONLY when the phone is asking for a
           page. Otherwise every failed request would get HTML back and
           look as though it had worked - including the question
           "is the laptop up". */
        if (event.request.mode === "navigate") { return caches.match("/"); }
        return Response.error();
      });
    })
  );
});
"""


def _copy_number(stamp):
    """Short number of the program file versions, for the stored-copy name.

    It has to change with EVERY update and not change on an ordinary
    restart - hence file dates and sizes rather than the start time.
    """
    import hashlib
    content = json.dumps(stamp or {}, sort_keys=True).encode("utf-8")
    return hashlib.sha256(content).hexdigest()[:12]


def file_stamp():
    """Size and date of every file that only takes effect after a restart.

    Serves to detect the case where a newer version of the program lies on
    disk than the one that is running. Without this the user sees new
    buttons in the browser calling into the old engine and gets a message
    that means nothing to them.
    """
    stamp = {}
    for name in CACHE_BUSTED_FILES:
        try:
            state = os.stat(os.path.join(PACKAGE_DIR, name))
            stamp[name] = [int(state.st_mtime), state.st_size]
        except OSError:
            stamp[name] = None
    return stamp


class RequestHandler(BaseHTTPRequestHandler):

    server_version = "chipbook"
    sys_version = ""

    def log_message(self, format, *args):
        return  # silence; we print the errors ourselves, in plain language

    # -------------------------------------------------------- responses

    def _send(self, code, content, content_type):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionAbortedError):
            pass  # the user closed the tab midway - this is not a failure

    def _json(self, code, data):
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                     "application/json; charset=utf-8")

    def _error(self, code, text):
        self._json(code, {"error_message": text})

    # ---------------------------------------------------------- gates

    def _is_local(self):
        """Whether the request came from the same machine the program runs on.

        This is the only distinction the whole lock for the phone stands on:
        the window on the laptop is fully trusted, everything else has to
        identify itself with the code. The address of the connection comes from
        the operating system, so it cannot be supplied in a header or forged
        from a page.

        CHECKING FOR 127.0.0.1 IS NOT ENOUGH - and that only showed up on
        screen. When a person opens chipbook on the laptop under the NETWORK
        address - the same one they type into the phone - the connection has
        the source address of the network card, not of the loopback. So the
        program was asking its own owner for the code on their own computer.
        The list of "our own" therefore also holds our own address on the
        network; impersonating it from another device is not possible, because
        the answers would go back to the real owner of that address anyway.
        """
        return self.client_address[0] in self.server.my_addresses

    def _remote_allowed(self, path):
        """Whether this address may be called from a device other than the laptop."""
        if path in REMOTE_ALLOWED:
            return True
        return (path.startswith("/api/jobs/")
                and path.endswith(REMOTE_ALLOWED_SUFFIXES))

    def _same_origin(self, origin_header):
        """Whether the request came from a page served by THIS server.

        The list of allowed addresses used to be written down hard (127.0.0.1
        and localhost). With listening on the network switched on that is too
        little: the phone comes in by the laptop's address on the network,
        which we do not know in advance - the laptop may have several cards,
        the Wi-Fi address changes when the network is switched, and with a
        hotspot it is different again. Writing them all out would be guessing.
        INSTEAD: the origin has to equal the address the browser called us by
        (the Host header). That is exactly the "same page" condition and it
        cannot be forged from somebody else's site - the Origin header is set
        by the browser, not by the page.
        """
        if origin_header in self.server.allowed_origins:
            return True
        host = self.headers.get("Host")
        return bool(host) and origin_header == (
            self.server.scheme + "://" + host)

    def _allowed(self):
        """A request to /api needs a token and the right origin.

        Only a page served by this server gets the token. Thanks to that a page
        from the internet opened in the same browser can neither read the
        database nor write to it.
        """
        origin_header = self.headers.get("Origin")
        if origin_header is not None and not self._same_origin(origin_header):
            return False
        given = self.headers.get("X-Chipbook-Token", "")
        if not secrets.compare_digest(given, self.server.token):
            return False
        # Every correct request from the window means the window is alive - so it
        # cancels the scheduled shutdown. Thanks to that refreshing the page does
        # not end the program, even though the browser managed to say goodbye.
        if self.path.partition("?")[0] != "/api/shutdown":
            self.server.cancel_shutdown()
        return True

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return {}
        if length <= 0 or length > 5 * 1024 * 1024:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    # ------------------------------------------------------- GET

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path in ("/", "/index.html"):
            return self._page()
        # The icons and the app description go WITHOUT A TOKEN, just like the
        # favicon: the browser asks for them by itself, before our code runs at
        # all. There is nothing to protect here - these are program files, not data.
        if path == "/favicon.ico":
            return self._icon()
        if path == "/icon.png":
            return self._icon_png()
        if path == "/manifest.json":
            return self._web_manifest()
        if path == "/sw.js":
            return self._service_worker()
        if not path.startswith("/api/"):
            return self._error(404, "There is no such page.")
        if not self._allowed():
            return self._error(403, "No access to this database.")

        params = _query_params(query)
        try:
            if path == "/api/status":
                now = file_stamp()
                stale = [n for n in CACHE_BUSTED_FILES
                               if now.get(n) != self.server.stamp.get(n)]
                return self._json(200, {
                    "job_count": self.server.catalog.job_count(),
                    "data_dir": self.server.catalog.data_dir,
                    "stale": stale,
                    # The version number has to be VISIBLE, not something to dig
                    # out - so that a screenshot from the user says by itself
                    # which program it came from.
                    "version": "%d (%s)" % (catalog.PROGRAM_VERSION,
                                           catalog.PROGRAM_DATE),
                    # The window has to know whether the program stays after the
                    # tab is closed - because then and only then it shows the
                    # "Quit program" button. A button visible always would be
                    # telling a lie on a computer with no network, where closing
                    # the window is enough.
                    "network_on": bool(self.server.network),
                    "is_local": self._is_local(),
                    # THE CODE IS NOT HERE AND THAT IS DELIBERATE. It does not
                    # sit on the screen all day - it appears only after
                    # clicking "New code" and only for a moment. Since the
                    # window does not display it by itself, there is no reason
                    # for it to receive it.
                    # The address for the phone MUST be visible in the window,
                    # not only in the console. From the moment the program
                    # starts from an icon (with no black window) there is no
                    # console at all - and nobody would know what to type into
                    # the phone.
                    # THE ADDRESS IS COMPUTED, NOT STORED: on another computer
                    # it will be different and the program reads it off that
                    # machine's network card.
                    "phone_address": self.server.phone_address or "",
                    # The window is to know whether the AI switch makes sense - and
                    # to get the model name, because it differs everywhere (CHIPBOOK_MODEL).
                    "model": ai.MODEL,
                })
            if path == "/api/model":
                # Separate from /api/status, because asking ollama whether it is
                # alive costs a network connection, and /api/status runs on EVERY
                # refresh of the window.
                return self._json(200, {"available": ai.available(),
                                        "model": ai.MODEL})
            if path == "/api/suggestions":
                return self._json(200, self.server.catalog.suggestions())
            if path == "/api/customers":
                return self._json(200,
                                  {"customers": self.server.catalog.customers()})

            if path == "/api/jobs":
                # THE CUSTOMER TAKES PRECEDENCE OVER THE SEARCH and that is
                # deliberate: when a person picked a customer from the list, they
                # pointed at it with a finger. Appending that to the search phrase
                # would mean looking for the WORD "ACME" everywhere - including in
                # the notes of other jobs, where it may turn up by accident.
                customer = params.get("customer", "").strip()
                if customer:
                    return self._json(200, {
                        "jobs": self.server.catalog.jobs_for_customer(customer),
                        "corrections": [], "skipped": [], "forms": [],
                        "phrase": "", "mode": "customer", "customer": customer})

                phrase = params.get("q", "").strip()
                if not phrase:
                    # NO LIMIT: the list is called "All jobs", so it has to
                    # show them all, and the counter beside it has to give
                    # the true number.
                    jobs = self.server.catalog.recent()
                    return self._json(200, {"jobs": jobs, "corrections": [],
                                            "skipped": [], "forms": [],
                                            "phrase": "", "mode": "recent"})
                result = self.server.catalog.search(phrase)
                return self._json(200, {
                    "jobs": result.jobs,
                    "corrections": [list(p) for p in result.corrections],
                    "skipped": result.skipped,
                    "forms": [[word, other] for word, other in result.forms],
                    "words": result.words,
                    "phrase": phrase,
                    "mode": "search",
                })
            if path.startswith("/api/files/") and path.endswith("/preview"):
                item = self.server.catalog.attachment(
                    _number(path[len("/api/files/"):-len("/preview")]))
                if item is None:
                    return self._error(404, "There is no such file in the database.")
                if not item["present"]:
                    return self._error(404, "That file is not on disk.")
                return self._json(200, setupsheet.describe(item["path"],
                                                     item["name"]))
            if path.startswith("/api/files/"):
                item = self.server.catalog.attachment(
                    _number(path[len("/api/files/"):]))
                if item is None:
                    return self._error(404, "There is no such file in the database.")
                if not item["present"]:
                    return self._error(
                        404, "The entry remembers this file, but it is no longer "
                             "on disk: " + item["path"])
                return self._send_file(
                    item, show=params.get("show") == "1")
            if path.startswith("/api/jobs/"):
                number = _number(path[len("/api/jobs/"):])
                if number is None:
                    return self._error(400, "Bad entry number.")
                record = self.server.catalog.job(number)
                if record is None:
                    return self._error(404, "There is no such entry.")
                return self._json(200, self._with_attachments(record))
        except catalog.ChipbookError as error:
            return self._error(400, str(error))
        except Exception as error:                      # noqa: BLE001
            return self._failure(error)
        return self._error(404, "There is no such address.")

    # ------------------------------------------------------- POST

    def do_POST(self):
        path, _, _ = self.path.partition("?")
        if not path.startswith("/api/"):
            return self._error(404, "There is no such address.")

        # THE ONLY ADDRESS THAT WORKS WITHOUT A TOKEN - because it serves
        # to obtain one. It stands BEFORE the gate deliberately and does
        # nothing but compare the code.
        if path == "/api/session":
            return self._session()

        if path == "/api/pairing-code":
            return self._new_code()

        if not self._allowed():
            return self._error(403, "No access to this database.")

        # THE GATE FOR WHAT IS ALLOWED FROM THE PHONE. It stands in the SERVER,
        # not in the window - a hidden button is not a safeguard. The window hides
        # the same things only so as not to promise what is not there.
        if not self._is_local() and not self._remote_allowed(path):
            return self._error(
                403, "This cannot be done from the phone. From the phone you "
                     "can search, look at jobs, ask the AI, append "
                     "notes and add a photo.")

        # CAREFUL: the branch for files MUST be handled BEFORE we touch the body
        # of the request. _body() reads the whole stream to turn it into JSON - if
        # it ran first, copying the file would wait forever for bytes that have
        # already been eaten.
        try:
            if path.startswith("/api/jobs/") and path.endswith("/files"):
                number = _number(path[len("/api/jobs/"):-len("/files")])
                if number is None:
                    return self._error(400, "Bad entry number.")
                from urllib.parse import unquote
                name = unquote(self.headers.get("X-File-Name", ""))
                try:
                    size_bytes = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    return self._error(400, "I do not know how big this file is.")
                item = self.server.catalog.add_attachment(
                    number, name, self.rfile, size_bytes)
                record = self.server.catalog.job(number)
                return self._json(200, {"attachment": item,
                                        "job": self._with_attachments(record)})
        except catalog.ChipbookError as error:
            return self._error(400, str(error))
        except Exception as error:                      # noqa: BLE001
            return self._failure(error)

        data = self._body()
        try:
            if path == "/api/shutdown":
                # The browser says the window is going away. We do not end at
                # once - the same goodbye comes on a page refresh (F5), and
                # then the new page cancels the shutdown with its first
                # request (see _allowed -> cancel_shutdown).
                #
                # WITH VISIBILITY ON THE NETWORK SWITCHED ON THE PROGRAM STAYS.
                # Ending together with the window was right on a single
                # computer. With a phone it means, however, that closing the
                # window on the laptop kills the database for the phone on the
                # other side of the shop floor - reported at once, on the first
                # use.
                #
                # THERE IS NO "QUIT PROGRAM" AND THAT IS THE OWNER'S DECISION,
                # taken after the cost was laid out. The button existed in the
                # window for half an hour and was deleted: chipbook is to run
                # in the background like any other program and end together
                # with the computer being switched off. When it has to be
                # stopped earlier - Task Manager, the pythonw.exe process.
                # Nothing is lost by that, because every job is saved at the
                # moment it is saved.
                if self.server.network:
                    return self._json(200, {"done": False})
                self.server.schedule_shutdown()
                return self._json(200, {"done": True})

            if path == "/api/ask":
                # The AI mode of the search. POST rather than GET, for two
                # reasons: the question is a whole sentence, and the answer can
                # take tens of seconds - this is not a read that can be repeated
                # in the background.
                question = str(data.get("question", "")).strip()
                if not question:
                    return self._error(400, "Empty question.")
                # `job` arrives when the person has already pointed at one job
                # among several candidates.
                number = _number(str(data.get("job", "")))
                # The conversation lives in the browser window, not on the server -
                # it arrives in full with every question. Thanks to that the server
                # does not have to remember any sessions.
                pairs = []
                for element in data.get("clarifications") or []:
                    if isinstance(element, (list, tuple)) and len(element) == 2:
                        pairs.append((str(element[0]), str(element[1])))
                answer = self.server.catalog.ask(
                    question, clarifications=pairs, number=number)
                return self._json(200, {
                    "kind": answer.kind,
                    "text": answer.text,
                    # THE LINE THE MODEL BASED ITS ANSWER ON.
                    # The window shows it under the bubble, and the
                    # program checks by itself whether it really exists.
                    "source": answer.source,
                    # FALSE DOES NOT MEAN "a bad answer" - it means
                    # "I did not confirm where the model saw this".
                    "source_confirmed": answer.source_confirmed,
                    "jobs": answer.jobs,
                    "differences": answer.differences,
                    "candidates": answer.candidates,
                    "model": answer.model_name,
                    "corrections": [list(p) for p in answer.corrections],
                    "skipped": answer.skipped,
                    "forms": [[word, other] for word, other in answer.forms],
                    "question": question,
                })

            if path == "/api/jobs":
                record = self.server.catalog.add_job(
                    name=data.get("name", ""),
                    customer=data.get("customer", ""),
                    material=data.get("material", ""),
                    notes=data.get("notes", ""),
                    # The order number - optional, the same in both
                    # forms. An entry made at the machine and a job
                    # made on the laptop are to have the same fields.
                    order_number=data.get("order_number", ""),
                )
                return self._json(200, self._with_attachments(record))

            if path == "/api/jobs/offline":
                # AN ENTRY CREATED AT THE MACHINE, LET IN IN THE EVENING.
                # It differs from /api/jobs in two things and both came out of
                # measurement, not out of caution:
                #   idempotency_key - a repeated send has to land in THE SAME job,
                #           because a stalled request gets through when the laptop
                #           comes back, and without this a twin job would appear;
                #   when  - the date from the phone, that is from the moment at the
                #           machine, and not from the moment of letting it into the
                #           database.
                # THE ANSWER SAYS WHETHER THE ENTRY IS NEW - the phone has to be
                # able to tell "accepted" from "I already had this", to know
                # whether it still has files to send.
                idempotency_key = str(data.get("idempotency_key", "") or "").strip()
                already_there = (self.server.catalog.job_by_key(idempotency_key) is not None
                           if idempotency_key else False)
                record = self.server.catalog.add_job(
                    name=data.get("name", ""),
                    customer=data.get("customer", ""),
                    material=data.get("material", ""),
                    notes=data.get("notes", ""),
                    idempotency_key=idempotency_key,
                    when=data.get("when", ""),
                    order_number=data.get("order_number", ""),
                )
                answer_ = self._with_attachments(record)
                answer_["new"] = not already_there
                return self._json(200, answer_)

            if path.startswith("/api/jobs/") and path.endswith("/fields"):
                # Correcting an entry that is already saved.
                # The notes field arrives only when it is to be changed - its
                # absence from the request means "do not touch". Appending a note
                # still has its own path and overwrites nothing.
                number = _number(path[len("/api/jobs/"):-len("/fields")])
                if number is None:
                    return self._error(400, "Bad entry number.")
                record = self.server.catalog.update_fields(
                    number,
                    name=data.get("name", ""),
                    customer=data.get("customer", ""),
                    material=data.get("material", ""),
                    notes=data.get("notes"),
                    order_number=data.get("order_number"),
                )
                return self._json(200, self._with_attachments(record))

            if path.startswith("/api/jobs/") and path.endswith("/delete"):
                number = _number(path[len("/api/jobs/"):-len("/delete")])
                if number is None:
                    return self._error(400, "Bad entry number.")
                return self._json(200, self.server.catalog.delete_job(number))

            if path.startswith("/api/files/") and path.endswith("/delete"):
                # DELETING ONE FILE, not the whole entry. Until now a
                # wrongly added file could only be thrown out together
                # with the job and its notes.
                # ONLY FROM THIS COMPUTER. The REMOTE_ALLOWED list does not
                # hold this address and must not: from the phone one may
                # ADD (notes, a photo), not delete. The phone is sometimes
                # in a pocket on the shop floor and one accidental touch
                # has no right to remove somebody's file.
                number = _number(path[len("/api/files/"):-len("/delete")])
                if number is None:
                    return self._error(400, "Bad file number.")
                return self._json(200,
                                  self.server.catalog.delete_attachment(number))

            if path.startswith("/api/files/") and path.endswith("/open"):
                # Opening the file WHERE IT LIES, with the default Windows
                # program. The browser cannot do that - it can only fetch a copy,
                # which on every click littered the Downloads folder.
                item = self.server.catalog.attachment(
                    _number(path[len("/api/files/"):-len("/open")]))
                if item is None:
                    return self._error(404, "There is no such file in the database.")
                if not item["present"]:
                    return self._error(
                        404, "The entry remembers this file, but it is no longer "
                             "on disk: " + item["path"])
                # ONLY FROM THIS COMPUTER. From the phone this address would open
                # the file on the LAPTOP, where nobody is standing - the user is
                # at the machine then. The window on the phone calls a different
                # address anyway (the file to look at on its own screen), but the
                # gate stands here, because a hidden button is not a safeguard.
                if not self._is_local():
                    return self._error(
                        403, "A file opens only on the computer the program "
                             "runs on.")
                if not hasattr(os, "startfile"):
                    return self._error(
                        400, "Opening files from the program works on Windows "
                             "only.")
                os.startfile(item["path"])
                return self._json(200, {"opened": item["name"],
                                        "path": item["path"]})

            if path.startswith("/api/jobs/") and path.endswith("/folder"):
                number = _number(path[len("/api/jobs/"):-len("/folder")])
                record = self.server.catalog.job(number) if number else None
                if record is None:
                    return self._error(404, "There is no such entry.")
                directory = self.server.catalog.job_dir(record)
                if not hasattr(os, "startfile"):
                    return self._error(
                        400, "Opening a folder works on Windows only. "
                             "The entry folder: " + directory)
                os.makedirs(directory, exist_ok=True)
                os.startfile(directory)
                return self._json(200, {"data_dir": directory})

            if path.startswith("/api/jobs/") and path.endswith("/notes"):
                raw_text = path[len("/api/jobs/"):-len("/notes")]
                number = _number(raw_text)
                if number is None:
                    return self._error(400, "Bad entry number.")
                record = self.server.catalog.append_note(
                    number, data.get("text", ""))
                return self._json(200, self._with_attachments(record))
        except catalog.ChipbookError as error:
            return self._error(400, str(error))
        except Exception as error:                      # noqa: BLE001
            return self._failure(error)
        return self._error(404, "There is no such address.")

    # -------------------------------------------------------- the rest

    def _session(self):
        """The phone gives the code and gets a token. That is the whole lock.

        WHY A CODE AND NOT A PASSWORD: the code has six digits and is copied
        off the laptop screen once, on one's own phone. A password that has to
        be invented and remembered ends up on a note by the monitor.
        WHY THAT IS ENOUGH: a million combinations against ten attempts per run
        of the program. After the tenth mistake the code stops working until
        chipbook is started again - so guessing one after another is pointless,
        not merely slow.
        """
        if not self.server.network or not self.server.code:
            return self._error(403, "This chipbook is not visible on the network.")
        if self.server.failed_attempts >= MAX_CODE_ATTEMPTS:
            return self._error(
                403, "Too many mistakes. Start chipbook on the computer "
                     "again to be able to try once more.")
        given = str(self._body().get("code", "")).strip()
        if not secrets.compare_digest(given, self.server.code):
            self.server.failed_attempts += 1
            return self._error(
                403, "The code does not match. Attempts left: %d"
                     % (MAX_CODE_ATTEMPTS - self.server.failed_attempts))
        self.server.failed_attempts = 0
        return self._json(200, {"token": self.server.token})

    def _new_code(self):
        """Draws the code anew and THROWS OUT every phone.

        WHY THIS EXISTS, given that the code is permanent: because a permanent
        code with no way to change it means that whoever saw it once has a way
        in forever. One click on the laptop and the phones have to identify
        themselves again.
        THE TOKEN GOES IN THE BIN TOO - and that is the essence of "throwing
        out". Changing the code alone would not cut off a phone that is ALREADY
        in: it holds a valid token and no longer needs the code. So we change
        both, and the window on the laptop reloads itself for a new one.
        ONLY FROM THIS COMPUTER. The phone cannot change the code, because then
        somebody who got in once could shut the door on the owner.
        """
        if not self.server.network:
            return self._error(403, "This chipbook is not visible on the network.")
        if not self._is_local():
            return self._error(
                403, "The code changes only on the computer the program "
                     "runs on.")
        path = os.path.join(self.server.catalog.data_dir, PHONE_CODE_FILE)
        try:
            os.remove(path)
        except OSError:
            pass
        self.server.code = phone_code(self.server.catalog.data_dir)
        self.server.token = secrets.token_urlsafe(24)
        self.server.failed_attempts = 0
        # THE NEW TOKEN GOES BACK TO THE WINDOW that asked for the change - and
        # only to it, because only it can reach here. Without this the page on the
        # laptop would have to reload, and then the fresh code would vanish from
        # the screen before a person managed to copy it down.
        return self._json(200, {"code": self.server.code,
                                "token": self.server.token})

    def _page(self):
        try:
            html = page_source()
        except OSError:
            return self._send(
                500, "The file web/index.html is missing from the package.".encode("utf-8"),
                "text/plain; charset=utf-8")
        # A PAGE SERVED OUTSIDE THIS COMPUTER GETS NO TOKEN.
        # Anyone who knew the address used to get one - and that was a hole in
        # itself: the token was to defend the database, and the program handed
        # it out at the door. Now the phone gets a page without a token, and
        # that page asks for the code shown in the window on the laptop.
        html = html.replace(
            TOKEN_PLACEHOLDER,
            self.server.token if self._is_local() else "")
        # THE HOME-SCREEN ICON GOES INTO THE PAGE ITSELF.
        # REPORTED FROM USE: after the move to the secured entrance, "add to
        # home screen" stopped taking the chipbook mark, although THE SAME icon
        # opened directly in Safari shows without fault (checked on the device).
        # A SUSPICION, NOT A CERTAINTY: the home-screen icon is fetched by a
        # different part of the system than Safari, and that part may not accept
        # a certificate installed by hand. Putting the image into the page
        # removes the need to fetch it separately - if the hypothesis is right,
        # that is enough.
        # Should it turn out not to be enough - this change breaks nothing
        # anyway, and /icon.png stays in place for the app description.
        html = html.replace(ICON_PLACEHOLDER, self._icon_in_page())
        # THE VERSION NUMBER GOES INTO THE PAGE ITSELF, not only into
        # /api/status.
        # REPORTED FROM USE: with the laptop shut there is no way to check which
        # version of the window the phone is holding - and without that every
        # "it still does not work" is guesswork, because there is no telling
        # whether the fix got there at all.
        html = html.replace(VERSION_PLACEHOLDER, str(catalog.PROGRAM_VERSION))
        self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")

    def _icon_in_page(self):
        """The icon address to put into the page: the image itself or a path.

        When the icon cannot be read, we fall back to the ordinary path instead
        of putting in something broken - the page has to work then too.
        """
        import base64
        data = largest_png_from_icon()
        if data is None:
            return "/icon.png"
        return "data:image/png;base64," + base64.b64encode(data).decode("ascii")

    def _icon_png(self):
        """The icon as a PNG - for the phone, for the home screen.

        WHY NOT A NEW FILE ON DISK: `chipbook.ico` holds seven images and EVERY
        one of them is already a PNG (see tools/make_icon.py). We pull out the
        largest instead of drawing a second file - because two files with the
        same drawing will sooner or later drift apart.
        Phones cannot read .ico for the home screen, so without this an iPhone
        shows a screenshot of the page instead of the mark.
        """
        data = largest_png_from_icon()
        if data is None:
            return self._error(404, "I cannot get an image out of chipbook.ico.")
        self._send(200, data, "image/png")

    def _service_worker(self):
        """The piece thanks to which the phone opens chipbook WITH NO LAPTOP.

        WHY THIS EXISTS. The user wants to start a job at the machine while the
        laptop is shut. The browser then stores a copy of the page at its own
        end and serves it when the server is not there. Without this file the
        icon on the home screen is only a shortcut to an address that does not
        answer with the laptop shut.

        REQUESTS TO /api/ NEVER COME FROM THE STORED COPY. They are either to
        get through for real or to fail honestly - otherwise the phone would
        show yesterday's database as today's, and that is worse than no
        connection.

        THE NUMBER OF THE STORED COPY COMES FROM THE FILE STAMP, that is from
        the dates and sizes of web/index.html and the rest. Thanks to that an update of
        the program invalidates the old copy in the phone by itself - without it
        the user would be looking at the previous version of the window after an
        update and reporting bugs that are already gone.
        """
        number = _copy_number(self.server.stamp)
        content = SW_JS % {"copy_number": number}
        self._send(200, content.encode("utf-8"),
                     "text/javascript; charset=utf-8")

    def _web_manifest(self):
        """The description file for Android - so that "add to home screen"
        gives an icon and full-screen opening instead of an ordinary tab.

        ASSEMBLED IN CODE, not kept as a file: it holds only the name and the
        colours, which stand in the program anyway. One file less to look after
        in the package for the user.
        """
        description = {
            "name": "chipbook",
            "short_name": "chipbook",
            "start_url": "/",
            "display": "standalone",
            "background_color": "#13161b",
            "theme_color": "#13161b",
            "icons": [{"src": "/icon.png", "sizes": "256x256",
                       "type": "image/png", "purpose": "any"}],
        }
        self._send(200, json.dumps(description).encode("utf-8"),
                     "application/manifest+json")

    def _icon(self):
        """The icon shown in the browser tab.

        THE SAME FILE AS THE DESKTOP SHORTCUT ICON. One source, so a change in
        make_icon.py reaches both places at once and two images of the same
        program never drift apart.

        DELIBERATELY BEFORE THE TOKEN GATE: the browser asks for the icon by
        itself, from an address without our token - behind the gate it would
        never arrive. There is nothing to protect here: the file lies in the
        PROGRAM directory, not in the directory with the user's data.
        """
        try:
            with open(ICON_FILE, "rb") as file:
                data = file.read()
        except OSError:
            return self._error(404, "The file web/chipbook.ico is missing from the package.")
        self._send(200, data, "image/x-icon")

    def _with_attachments(self, record):
        record["data_dir"] = self.server.catalog.job_dir(record)
        attachments = self.server.catalog.attachments(record["id"])
        for item in attachments:
            item["viewable"] = bool(
                item["present"] and setupsheet.can_display(item["name"]))
            item["setup_sheet"] = (setupsheet.extension(item["name"])
                                 in setupsheet.SETUP_SHEET_EXTENSIONS)
        record["attachments"] = attachments
        return record

    def _send_file(self, item, show=False):
        """Hands over a file. `show` means "to look at", not "to download".

        WHY THE DISTINCTION: an attachment is opened by the SERVER, because the
        browser on the laptop can only fetch a copy into Downloads. From the
        phone that same rule means, however, "open the file at the other end of
        the workshop" - caught in use when a photo taken with the phone was
        opened with the Open button and it opened on the laptop instead. So the
        phone gets the file to look at on its own screen.
        THE TYPE IS GIVEN ONLY FOR KNOWN EXTENSIONS (INLINE_CONTENT_TYPES);
        anything else loads as before, that is as a download.
        """
        from urllib.parse import quote
        extension = os.path.splitext(item["name"])[1].lower()
        content_type = INLINE_CONTENT_TYPES.get(extension) if show else None
        self.send_response(200)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(item["size_bytes"]))
        self.send_header(
            "Content-Disposition",
            ("inline" if content_type else "attachment")
            + "; filename*=UTF-8''" + quote(item["name"]))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            with open(item["path"], "rb") as file:
                while True:
                    chunk = file.read(1024 * 256)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def _failure(self, error):
        import traceback
        traceback.print_exc()
        self._error(500, "Something went wrong inside the program. The details "
                        "are printed in the console window. No data has "
                        "been changed.")


def _query_params(query):
    from urllib.parse import parse_qs
    return {k: v[0] for k, v in parse_qs(query, keep_blank_values=True).items()}


def _number(text):
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def phone_code(data_dir):
    """The six-digit code the phone identifies itself to the program with.

    IT LIES IN THE DATA DIRECTORY, not in the program directory - thanks to
    that it survives every update and the user types it into the phone once,
    not after every fix.
    When the file is missing or its content looks nothing like a code, we
    draw a new one. When the write fails (a read-only data directory) the
    code still works - it will simply be different after a restart; better
    that than a program that does not come up.
    """
    path = os.path.join(data_dir, PHONE_CODE_FILE)
    try:
        with open(path, encoding="ascii") as file:
            code = file.read().strip()
        if len(code) == 6 and code.isdigit():
            return code
    except (OSError, ValueError):
        pass
    code = "%06d" % secrets.randbelow(1000000)
    try:
        with open(path, "w", encoding="ascii") as file:
            file.write(code + "\n")
    except OSError:
        pass
    return code


def largest_png_from_icon(path=None):
    """The largest PNG image hidden inside chipbook.ico, or None.

    Pulled out of _icon_png because the page needs the same image now too
    (we paste it into the page instead of making the phone fetch it
    separately). No second file with the same drawing - two files sooner or
    later drift apart.
    """
    try:
        with open(path or ICON_FILE, "rb") as file:
            data = file.read()
    except OSError:
        return None
    try:
        count = struct.unpack("<H", data[4:6])[0]
        best = None
        for number in range(count):
            job = data[6 + number * 16:22 + number * 16]
            side = job[0] or 256
            size_bytes, where = struct.unpack("<II", job[8:16])
            image = data[where:where + size_bytes]
            if image[:8] != b"\x89PNG\r\n\x1a\n":
                continue              # not a PNG - we skip it, we do not guess
            if best is None or side > best[0]:
                best = (side, image)
    except (struct.error, IndexError):
        return None
    return best[1] if best else None
