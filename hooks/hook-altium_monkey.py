"""
PyInstaller hook for altium_monkey.

``altium_monkey/__init__.py`` marks its declared release surface at import time
(``_mark_declared_public_surfaces()`` runs on the last line of the module).  That
function walks the ``_EXTRA_PUBLIC_SURFACES`` dict and calls
``importlib.import_module(f".{module_name}", __name__)`` on each key, so the
module names exist only as dict literals and PyInstaller's static analysis
cannot see them.

Most of those modules happen to be pulled in anyway by a static
``from .x import y`` elsewhere in the package, but not all of them:
``altium_pcb_ipc2581_writer`` has no static reference anywhere, so without this
hook ``import altium_monkey`` raises

    ModuleNotFoundError: No module named 'altium_monkey.altium_pcb_ipc2581_writer'

in the frozen app — which breaks loading any .PcbDoc.  Collecting every
submodule keeps the bundle correct no matter which modules the release surface
happens to reference statically, so an altium_monkey upgrade cannot silently
reintroduce the failure.

``altium_draftsman`` also reads packaged data through ``importlib.resources``
(``data/draftsman/blank_ad25.PCBDwf.xml``), which has to be collected explicitly.
"""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hiddenimports = collect_submodules("altium_monkey")
datas = collect_data_files("altium_monkey")
