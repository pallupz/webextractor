"""Importing this package registers all built-in extractors.

To add a site: drop a module here with an @register-decorated Extractor and
import it below.
"""

from . import generic, reddit  # noqa: F401  (imported for their @register side effect)
