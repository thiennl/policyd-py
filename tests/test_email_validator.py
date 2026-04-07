import os
import tempfile
import unittest
from unittest import IsolatedAsyncioTestCase

from policyd_py.config.settings import ValidationConfig
from policyd_py.validation.validator import EmailValidator, _extract_domain, _load_file_to_set


class ExtractDomainTests(unittest.TestCase):
    def test_valid_email(self):
        self.assertEqual(_extract_domain("user@example.com"), "example.com")

    def test_empty_string(self):
        self.assertEqual(_extract_domain(""), "")

    def test_no_at_sign(self):
        self.assertEqual(_extract_domain("nodomain"), "")


class LoadFileToSetTests(unittest.TestCase):
    def test_empty_path_returns_empty_set(self):
        self.assertEqual(_load_file_to_set(""), set())

    def test_missing_file_returns_empty_set(self):
        self.assertEqual(_load_file_to_set("/nonexistent/path"), set())

    def test_loads_lines_skipping_comments_and_blanks(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("alpha@example.com\n\n# comment\nbeta@example.com\n")
            f.flush()
            path = f.name
        try:
            result = _load_file_to_set(path)
            self.assertEqual(result, {"alpha@example.com", "beta@example.com"})
        finally:
            os.unlink(path)


class EmailValidatorSyntaxTests(unittest.TestCase):
    def _make(self, **overrides):
        cfg = ValidationConfig(**overrides)
        return EmailValidator(cfg)

    def test_sender_syntax_disabled_by_default(self):
        v = self._make()
        ok, msg = v.validate_sender_syntax("not-an-email")
        self.assertTrue(ok)

    def test_sender_syntax_valid(self):
        v = self._make(validate_sender_syntax=True)
        ok, msg = v.validate_sender_syntax("user@example.com")
        self.assertTrue(ok)

    def test_sender_syntax_invalid(self):
        v = self._make(validate_sender_syntax=True)
        ok, msg = v.validate_sender_syntax("bad")
        self.assertFalse(ok)
        self.assertIn("syntax", msg.lower())

    def test_recipient_syntax_disabled_by_default(self):
        v = self._make()
        ok, msg = v.validate_recipient_syntax("not-an-email")
        self.assertTrue(ok)

    def test_recipient_syntax_valid(self):
        v = self._make(validate_recipient_syntax=True)
        ok, msg = v.validate_recipient_syntax("rcpt@example.com")
        self.assertTrue(ok)

    def test_recipient_syntax_invalid(self):
        v = self._make(validate_recipient_syntax=True)
        ok, msg = v.validate_recipient_syntax("bad")
        self.assertFalse(ok)


class EmailValidatorDeliverabilityTests(IsolatedAsyncioTestCase):
    def _make(self, **overrides):
        cfg = ValidationConfig(**overrides)
        return EmailValidator(cfg)

    async def test_disabled_by_default(self):
        v = self._make()
        ok, msg = await v.validate_recipient_deliverability("user@nonexistent.invalid")
        self.assertTrue(ok)

    async def test_missing_domain_rejected(self):
        v = self._make(validate_recipient_deliverability=True)
        ok, msg = await v.validate_recipient_deliverability("nouser")
        self.assertFalse(ok)
        self.assertIn("domain", msg.lower())


class EmailValidatorBlacklistTests(unittest.TestCase):
    def _make_with_blacklists(self, sender_list=None, recipient_list=None, domain_list=None):
        cfg = ValidationConfig(enable_blacklist=True)
        v = EmailValidator(cfg)
        if sender_list is not None:
            v.sender_blacklist = sender_list
        if recipient_list is not None:
            v.recipient_blacklist = recipient_list
        if domain_list is not None:
            v.domain_blacklist = domain_list
        return v

    def test_sender_blacklist_hit(self):
        v = self._make_with_blacklists(sender_list={"bad@example.com"})
        blocked, msg = v.check_sender_blacklist("bad@example.com")
        self.assertTrue(blocked)
        self.assertIn("blacklisted", msg.lower())

    def test_sender_blacklist_miss(self):
        v = self._make_with_blacklists(sender_list={"bad@example.com"})
        blocked, msg = v.check_sender_blacklist("good@example.com")
        self.assertFalse(blocked)

    def test_recipient_blacklist_hit(self):
        v = self._make_with_blacklists(recipient_list={"spam@example.com"})
        blocked, msg = v.check_recipient_blacklist("spam@example.com")
        self.assertTrue(blocked)

    def test_domain_blacklist_hit(self):
        v = self._make_with_blacklists(domain_list={"evil.com"})
        blocked, msg = v.check_domain_blacklist("evil.com")
        self.assertTrue(blocked)

    def test_blacklist_disabled(self):
        cfg = ValidationConfig(enable_blacklist=False)
        v = EmailValidator(cfg)
        v.sender_blacklist = {"bad@example.com"}
        blocked, msg = v.check_sender_blacklist("bad@example.com")
        self.assertFalse(blocked)


class EmailValidatorBlacklistReloadTests(IsolatedAsyncioTestCase):
    async def test_reload_blacklists_from_files(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as sf:
            sf.write("bad@sender.com\n")
            sf.flush()
            sender_path = sf.name
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as rf:
            rf.write("bad@recipient.com\n")
            rf.flush()
            recipient_path = rf.name
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as df:
            df.write("evil.com\n")
            df.flush()
            domain_path = df.name

        try:
            cfg = ValidationConfig(
                enable_blacklist=True,
                sender_blacklist_file=sender_path,
                recipient_blacklist_file=recipient_path,
                domain_blacklist_file=domain_path,
            )
            v = EmailValidator(cfg)
            await v.start()
            try:
                self.assertIn("bad@sender.com", v.sender_blacklist)
                self.assertIn("bad@recipient.com", v.recipient_blacklist)
                self.assertIn("evil.com", v.domain_blacklist)
            finally:
                await v.stop()
        finally:
            for p in (sender_path, recipient_path, domain_path):
                os.unlink(p)

    async def test_reload_handles_missing_files(self):
        cfg = ValidationConfig(
            enable_blacklist=True,
            sender_blacklist_file="/nonexistent/sender.txt",
        )
        v = EmailValidator(cfg)
        await v.reload_blacklists()
        self.assertEqual(v.sender_blacklist, set())
