"""Distribution lifecycle checks in isolated directories; never touch the desktop."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('distribute', ROOT / 'scripts/distribute.py')
distribute = importlib.util.module_from_spec(spec)
spec.loader.exec_module(distribute)


class DistributionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {'XDG_CONFIG_HOME': self.tmp.name})
        env.start()
        self.addCleanup(env.stop)

    def test_install_update_remove_preserves_unrelated_and_state(self):
        state = Path(self.tmp.name) / 'user-backup.json'
        state.write_text('valuable')
        distribute.install(True)
        dest = distribute.destination()
        self.assertTrue((dest / 'qml/MouseMap.qml').is_file())
        self.assertFalse(any(p.is_symlink() for p in dest.rglob('*')))
        # A prior helper invocation may have generated standard bytecode.
        import py_compile
        py_compile.compile(str(dest / 'backend/op1we/__init__.py'), doraise=True)
        distribute.install(True)
        self.assertFalse(any(dest.rglob('*.pyc')))
        extra = dest / 'my-notes.txt'
        extra.write_text('keep')
        with self.assertRaises(ValueError):
            distribute.install(True)
        distribute.uninstall()
        self.assertEqual(extra.read_text(), 'keep')
        self.assertEqual(state.read_text(), 'valuable')
        self.assertFalse((dest / 'qml/Panel.qml').exists())

    def test_unmanaged_and_modified_installations_refused(self):
        dest = distribute.destination()
        dest.mkdir(parents=True)
        with self.assertRaises(ValueError):
            distribute.install(True)
        with self.assertRaises(ValueError):
            distribute.uninstall()
        dest.rmdir()
        distribute.install(True)
        (dest / 'README.md').write_text('local changes')
        with self.assertRaises(ValueError):
            distribute.install(True)
        with self.assertRaises(ValueError):
            distribute.uninstall()
        self.assertEqual((dest / 'README.md').read_text(), 'local changes')

    def test_symlink_destination_refused(self):
        target = Path(self.tmp.name) / 'unrelated'
        target.mkdir()
        dest = distribute.destination()
        dest.parent.mkdir(parents=True)
        dest.symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError):
            distribute.install(True)
        with self.assertRaises(ValueError):
            distribute.uninstall()

    def test_runtime_allowlist_excludes_development_and_secrets(self):
        names = distribute.runtime_files()
        self.assertIn('qml/MouseMap.qml', names)
        self.assertFalse(any(n.startswith(('tests/', '.git/', '.github/')) or
                             '__pycache__' in n or n.endswith('.pyc') for n in names))
