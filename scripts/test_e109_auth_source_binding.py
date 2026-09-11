"""Inspect the frozen-module binding without executing PyInstaller or an App."""
import ast
from pathlib import Path
import unittest
from scripts import build_e108_user_candidate as build


class AuthSourceBindingTests(unittest.TestCase):
    def test_exact_current_authentication_modules_are_rebound_without_build(self):
        project = Path(__file__).resolve().parents[1]
        names = build.auth_source_modules()
        self.assertEqual(set(names), {'app.enterprise.identity.' + name for name in
            ('config', 'issuer_migration', 'oidc', 'keycloak', 'runtime')})
        for name, path in names.items():
            self.assertTrue(path.is_relative_to(project / 'backend/app/enterprise/identity'))
            compile(path.read_bytes(), str(path), 'exec')
        # Execute only the bounded AST binding block with a synthetic stale TOC.
        # No PyInstaller import, EXE/PYZ invocation or filesystem writes.
        parsed = ast.parse((project / 'release/real-delivery-app.spec').read_text(encoding='utf-8'))
        start = next(i for i, n in enumerate(parsed.body) if isinstance(n, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == 'auth_sources' for t in n.targets))
        block = ast.Module(body=parsed.body[start:start+3], type_ignores=[])
        stale = [(name, '/stale/' + name, 'PYMODULE') for name in names if not name.endswith('issuer_migration')]
        retained = ('dependency.unchanged', '/seed/dependency.py', 'PYMODULE')
        env = {'auth_source_modules':build.auth_source_modules, 'pure':stale + [retained]}
        exec(compile(block, '<auth-source-binding>', 'exec'), env)
        self.assertIn(retained, env['pure'])
        for name, source in names.items():
            self.assertEqual([row for row in env['pure'] if row[0] == name], [(name, str(source), 'PYMODULE')])


if __name__ == '__main__':
    unittest.main()
