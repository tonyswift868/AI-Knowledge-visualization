"""Offline contract checks; no network, admin connection or secrets required."""
import ast
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parent


class PolicyContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = json.loads((ROOT / 'realm-policy.json').read_text(encoding='utf-8'))
        cls.nginx = (ROOT / 'nginx.edge.conf').read_text(encoding='utf-8')

    def test_registration_closed_and_legacy_identity_preserved(self):
        settings = self.policy['realm_settings']
        self.assertFalse(settings['registrationAllowed'])
        self.assertFalse(settings['registrationEmailAsUsername'])
        self.assertTrue(settings['verifyEmail'])
        actions = self.policy['required_actions']
        self.assertTrue(actions['VERIFY_EMAIL']['defaultAction'])
        self.assertLess(actions['VERIFY_EMAIL']['priority'], actions['UPDATE_PASSWORD']['priority'])
        self.assertEqual(self.policy['email_required_roles'], ['user'])
        self.assertFalse(actions['VERIFY_PROFILE']['enabled'])

    def test_default_role_and_captcha_gate(self):
        self.assertEqual(self.policy['default_new_user_role'], 'LEARNER')
        self.assertFalse(self.policy['recaptcha']['enabled'])
        self.assertTrue(self.policy['recaptcha']['keys_required'])

    def test_password_policy(self):
        password = self.policy['realm_settings']['passwordPolicy']
        for required in ['hashAlgorithm(argon2)', 'length(15)', 'maxLength(128)']:
            self.assertIn(required, password)
        for forbidden in ['upperCase(', 'lowerCase(', 'digits(', 'specialChars(', 'forceExpiredPasswordChange(']:
            self.assertNotIn(forbidden, password)
        pattern = password.split('regexPattern(', 1)[1][:-1]
        regex = re.compile(pattern)
        self.assertIsNone(regex.fullmatch('password' * 2))
        self.assertIsNone(regex.fullmatch(('password' * 2).upper()))
        self.assertIsNotNone(regex.fullmatch('violet meadow travels beside a quiet river'))
        self.assertIsNotNone(regex.fullmatch('x' * 64))

    def test_temporary_lockout_and_reset(self):
        settings = self.policy['realm_settings']
        self.assertTrue(settings['bruteForceProtected'])
        self.assertFalse(settings['permanentLockout'])
        self.assertEqual(settings['failureFactor'], 5)
        self.assertEqual(settings['actionTokenGeneratedByUserLifespan'], 600)
        self.assertFalse(self.policy['reset_flow']['reset_otp'])
        self.assertTrue(self.policy['reset_flow']['force_login'])

    def test_proxy_trust_and_no_append(self):
        self.assertEqual(self.policy['proxy']['trusted_addresses'], ['172.18.0.5'])
        self.assertNotIn('$proxy_add_x_forwarded_for', self.nginx)
        self.assertIn('proxy_set_header X-Forwarded-For $remote_addr;', self.nginx)
        self.assertIn('proxy_set_header Forwarded "";', self.nginx)
        self.assertIn('proxy_set_header X-Forwarded-Port 18080;', self.nginx)
        self.assertNotIn('0.0.0.0/0', self.nginx)
        self.assertNotIn('::/0', self.nginx)

    def test_post_only_rate_limits_and_static_preservation(self):
        self.assertIn('limit_req_status 429;', self.nginx)
        self.assertEqual(self.nginx.count('limit_req_zone '), 3)
        for line in self.nginx.splitlines():
            if '$binary_remote_addr;' in line:
                self.assertIn('~^POST:', line)
        self.assertIn('(VERIFY_EMAIL|UPDATE_EMAIL)', self.nginx)
        self.assertIn('if ($e109_registration_denied) { return 403; }', self.nginx)
        self.assertIn('/mentorflow/updates/e108/', self.nginx)

    def test_secret_template_and_source_syntax(self):
        config = json.loads((ROOT / 'smtp.example.json').read_text())
        self.assertEqual(config['SMTP_SECRET_ENV'], 'SMTP_SECRET')
        self.assertNotIn('SMTP_SECRET', config)
        self.assertTrue(config['SMTP_STARTTLS'])
        for source in ROOT.glob('*.py'):
            ast.parse(source.read_text(encoding='utf-8'), filename=str(source))


if __name__ == '__main__':
    unittest.main()
