"""Installed-runtime packaging. Brak wywołań modelu; FACTORY_NATIVE_ROOT wskazuje izolowany, przypięty kandydat.

Every top-level module of the native overlay must be listed in the native pyproject py-modules, otherwise an
installed runtime (editable or wheel) cannot import it from the console entry point and the start gates refuse.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import tomllib
import unittest

HERE = Path(__file__).resolve().parent
NATIVE = Path(os.environ['FACTORY_NATIVE_ROOT']).resolve(strict=True)
OVERLAY = json.loads((HERE.parent / 'native_integration' / 'native_overlay.json').read_text())


class OverlayPackaging(unittest.TestCase):
    def modules(self):
        return tomllib.loads((NATIVE / 'pyproject.toml').read_text())['tool']['setuptools']['py-modules']

    def test_every_top_level_overlay_module_is_packaged(self):
        top = {name[:-3] for name in OVERLAY['files'] if '/' not in name and name.endswith('.py')}
        self.assertEqual(sorted(top - set(self.modules())), [])

    def test_every_packaged_module_exists_in_native(self):
        for module in self.modules():
            with self.subTest(module=module):
                self.assertTrue((NATIVE / (module + '.py')).is_file())
