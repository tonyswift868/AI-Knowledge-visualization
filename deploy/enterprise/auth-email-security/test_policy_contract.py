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
        self.assertTrue(actions['UPDATE_PASSWORD']['enabled'])
        self.assertFalse(actions['UPDATE_PASSWORD']['defaultAction'])
        self.assertLess(actions['VERIFY_EMAIL']['priority'], actions['UPDATE_PASSWORD']['priority'])
        self.assertEqual(self.policy['email_required_roles'], ['user'])
        self.assertFalse(actions['VERIFY_PROFILE']['enabled'])

    def test_default_role_and_captcha_gate(self):
        self.assertEqual(self.policy['default_new_user_role'], 'LEARNER')
        self.assertFalse(self.policy['recaptcha']['enabled'])
        self.assertTrue(self.policy['recaptcha']['keys_required'])

    def test_password_policy(self):
        password = self.policy['realm_settings']['passwordPolicy']
        for required in ['hashAlgorithm(argon2)', 'length(8)', 'maxLength(128)']:
            self.assertIn(required, password)
        for forbidden in ['upperCase(', 'lowerCase(', 'digits(', 'specialChars(', 'forceExpiredPasswordChange(']:
            self.assertNotIn(forbidden, password)
        pattern = password.split('regexPattern(', 1)[1].split(') and passwordBlacklist(', 1)[0]
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

    def test_brute_force_bounds_and_unique_email(self):
        settings = self.policy['realm_settings']
        self.assertFalse(settings['duplicateEmailsAllowed'])
        self.assertEqual(settings['minimumQuickLoginWaitSeconds'], 60)
        self.assertEqual(settings['waitIncrementSeconds'], 60)
        self.assertEqual(settings['maxFailureWaitSeconds'], 900)
        self.assertEqual(settings['quickLoginCheckMilliSeconds'], 1000)
        self.assertEqual(settings['maxDeltaTimeSeconds'], 43200)
        self.assertEqual(self.policy['unmanaged_attributes'], 'DISABLED')

    def test_rate_limit_map_matches_auth_posts_and_not_assets_or_gets(self):
        # Exercise expressions extracted from the checked-in nginx source. This
        # is a routing contract, not evidence of a running nginx rate limiter.
        rules = re.findall(r'^\s*~(\S+)\s+\$binary_remote_addr;', self.nginx, re.M)
        self.assertEqual(len(rules), 4)
        matches = lambda value: any(re.search(rule, value) for rule in rules)
        targets = [
            '/protocol/openid-connect/token', '/login-actions/authenticate',
            '/login-actions/registration', '/login-actions/reset-credentials',
            '/login-actions/send-verify-email', '/login-actions/email-verification',
            '/login-actions/required-action',
        ]
        for target in targets:
            with self.subTest(target=target):
                self.assertTrue(matches('POST:/realms/mentorflow' + target))
                for method in ('GET', 'HEAD', 'OPTIONS'):
                    self.assertFalse(matches(method + ':/realms/mentorflow' + target))
        self.assertFalse(matches('POST:/resources/theme/login.css'))
        for zone, rate, burst in [('token', 30, 20), ('login', 10, 10), ('email', 3, 3)]:
            self.assertIn(f'zone=e109_auth_{zone}:1m rate={rate}r/m;', self.nginx)
            self.assertIn(f'limit_req zone=e109_auth_{zone} burst={burst} nodelay;', self.nginx)

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
        self.assertIn('/login-actions/required-action$ $binary_remote_addr;', self.nginx)
        self.assertNotIn('$arg_execution', self.nginx)
        self.assertIn('if ($e109_registration_denied) { return 403; }', self.nginx)
        self.assertIn('/mentorflow/updates/e108/', self.nginx)

    def test_secret_template_and_source_syntax(self):
        config = json.loads((ROOT / 'smtp.example.json').read_text())
        self.assertEqual(config['SMTP_SECRET_ENV'], 'SMTP_SECRET')
        self.assertNotIn('SMTP_SECRET', config)
        self.assertTrue(config['SMTP_STARTTLS'])
        for source in ROOT.glob('*.py'):
            ast.parse(source.read_text(encoding='utf-8'), filename=str(source))


    def test_full_common_password_list_remains_required_at_eight_characters(self):
        spec = self.policy['password_denylist']
        self.assertTrue(spec['native_file_provider_enabled'])
        self.assertEqual(spec['source_count'], 100000)
        self.assertIn('passwordBlacklist(' + spec['file_name'] + ')',
                      self.policy['realm_settings']['passwordPolicy'])
        compose = (ROOT.parent / 'staging/rp05/compose.account-core-mvp.yml').read_text(encoding='utf-8')
        self.assertIn('/opt/keycloak/data/password-blacklists:ro', compose)

    def test_username_is_unique_without_composition_constraints(self):
        username = self.policy['username_policy']
        self.assertTrue(username['unique'])
        self.assertTrue(username['trim_input'])
        self.assertEqual(username['validations']['length'], {'min': 1, 'max': 255})
        self.assertEqual(set(username['validations']), {'length', 'pattern'})
        self.assertEqual(username['validations']['pattern']['pattern'], r'^[^\p{Cc}]+$')
        self.assertEqual(username['duplicate_message'], '该账号已被使用，请换一个。')

    def test_atomic_reset_uses_the_realm_password_policy(self):
        source = (ROOT.parent / 'native-identity-verifier/AtomicResetPassword.java').read_text(encoding='utf-8')
        self.assertIn('getProvider(PasswordPolicyManagerProvider.class)', source)
        self.assertIn('.validate(context.getRealm(), context.getUser(), password)', source)


if __name__ == '__main__':
    unittest.main()
