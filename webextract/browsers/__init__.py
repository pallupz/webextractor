"""Browser backends. Importing this package registers Firefox and Chrome.

To add a browser: drop a module here with a @register-decorated Browser and
import it below.
"""

from .base import Browser, get_browser, names, register  # noqa: F401
from . import chrome, firefox  # noqa: F401  (imported for their @register side effect)
