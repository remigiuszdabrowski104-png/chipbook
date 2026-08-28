"""Entry point:  python -m chipbook

Opens the catalogue window in the browser. Everything the program needs
lives inside this package - the interface server, the setup-sheet reader
and the page itself.
"""
from .server import main

if __name__ == "__main__":
    main()
