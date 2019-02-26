"""
Brew style python CLI installation
"""

import runez


__version__ = runez.get_version(__name__)
runez.system.AbortException = SystemExit
