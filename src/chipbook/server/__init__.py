"""chipbook - the local interface server.

Run with:  python -m chipbook

Opens a window in the browser. The server listens EXCLUSIVELY on
127.0.0.1, that is on this computer - nothing goes out to the network
and nobody from outside can connect.

Division of roles:
    catalog.py      - data, knows nothing about the interface
    server/routes   - turns browser requests into calls into the catalogue
    server/app      - starting, ports, shutting down, the phone
    server/tls      - our own authority and certificate, for the phone
    web/index.html  - the look, knows nothing about SQL

Thanks to this division, replacing the window alone (say with a separate
program with its own window) touches neither the database nor the logic.
"""

from . import app
from . import routes
from . import tls
from .app import build, main
