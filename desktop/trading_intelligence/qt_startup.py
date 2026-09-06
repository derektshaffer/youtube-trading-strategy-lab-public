"""Validate the source environment's Qt platform plugins before starting services."""

from __future__ import annotations

import os
from pathlib import Path
import stat
import sys


def prepare_platform_plugins() -> Path:
    from PySide6.QtCore import QCoreApplication, QLibraryInfo, QPluginLoader

    plugins = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)).resolve()
    environment = Path(sys.prefix).resolve()
    if not plugins.is_relative_to(environment):
        raise RuntimeError("Qt plugins must come from this development Python environment.")
    platforms = plugins / "platforms"
    selected = os.environ.get("QT_QPA_PLATFORM") or ("cocoa" if sys.platform == "darwin" else "xcb")
    selected = selected.split(":", 1)[0]
    # macOS hidden file flags exclude plugins from Qt's directory scan even
    # though importing PySide6 and loading a plugin by absolute path both work.
    # Repair at every dev launch; never change the packaged app or global Qt.
    if sys.platform == "darwin":
        for path in (plugins, platforms, *platforms.glob("*.dylib")):
            flags = path.stat().st_flags
            if flags & stat.UF_HIDDEN:
                os.chflags(path, flags & ~stat.UF_HIDDEN)
    available = set()
    for path in platforms.iterdir():
        if path.is_file():
            metadata = QPluginLoader(str(path)).metaData()
            available.update(metadata.get("MetaData", {}).get("Keys", []))
    if selected not in available:
        raise RuntimeError(
            f"The development Qt platform plugin '{selected}' is missing or unreadable in {platforms}. "
            "Repair the development PySide6 installation before launching."
        )
    QCoreApplication.addLibraryPath(str(plugins))
    return platforms
