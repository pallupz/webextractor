"""Browser backends. Importing this package registers Firefox and Chrome.

To add a browser: drop a module here with a @register-decorated Browser and
import it below.
"""

from .base import (  # noqa: F401
    Browser,
    available,
    get_browser,
    names,
    register,
    resolve_engine,
)
from . import chrome, firefox  # noqa: F401  (imported for their @register side effect)
