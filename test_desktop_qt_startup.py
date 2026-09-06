import os
from pathlib import Path
import stat
import sys

import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import QLibraryInfo
from desktop.trading_intelligence.qt_startup import prepare_platform_plugins


def test_dev_startup_repairs_hidden_platform_plugins(monkeypatch, tmp_path):
    # Use real installed Qt metadata, but alter only private fixture copies.
    import shutil
    original = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)) / 'platforms'
    platforms = tmp_path / 'plugins' / 'platforms'
    platforms.mkdir(parents=True)
    for source in original.glob('*.dylib'):
        shutil.copyfile(source, platforms / source.name)
    if sys.platform != 'darwin':
        pytest.skip('macOS hidden platform plugin regression')
    monkeypatch.setattr(sys, 'prefix', str(tmp_path))
    monkeypatch.setattr(QLibraryInfo, 'path', lambda _: str(platforms.parent))
    monkeypatch.setenv('QT_QPA_PLATFORM', 'cocoa')
    paths = [platforms.parent, platforms, *platforms.iterdir()]
    for p in paths:
        os.chflags(p, p.stat().st_flags | stat.UF_HIDDEN)
    assert prepare_platform_plugins() == platforms
    assert all(not p.stat().st_flags & stat.UF_HIDDEN for p in paths)
    assert prepare_platform_plugins() == platforms


def test_dev_startup_rejects_missing_plugin_before_worker_launch(monkeypatch, tmp_path):
    plugins = tmp_path / 'plugins'
    (plugins / 'platforms').mkdir(parents=True)
    monkeypatch.setattr(sys, 'prefix', str(tmp_path))
    monkeypatch.setattr(QLibraryInfo, 'path', lambda _: str(plugins))
    monkeypatch.setenv('QT_QPA_PLATFORM', 'cocoa')
    with pytest.raises(RuntimeError, match='missing or unreadable'):
        prepare_platform_plugins()


def test_dev_startup_does_not_modify_another_installation(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, 'prefix', str(tmp_path / 'venv'))
    monkeypatch.setattr(QLibraryInfo, 'path', lambda _: str(tmp_path / 'release' / 'plugins'))
    with pytest.raises(RuntimeError, match='development Python environment'):
        prepare_platform_plugins()
