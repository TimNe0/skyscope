"""Desktop test bootstrap.

The app lives at the repo root and its modules import each other relatively
(`from . import geo`), because on the badge the folder is imported as
`apps.<folder>.app`. To import those modules under CPython without depending on
what the checkout directory happens to be called, the repo root is registered as
a package named `skyscope`.
"""

import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if "skyscope" not in sys.modules:
    package = types.ModuleType("skyscope")
    package.__path__ = [ROOT]
    # Deliberately not executing the root __init__.py: it pulls in app.py,
    # which needs firmware modules that do not exist off-badge.
    sys.modules["skyscope"] = package
