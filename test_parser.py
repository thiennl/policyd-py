import unittest

from policyd_py.core.models import PolicyRequest


class ParserTests(unittest.TestCase):
    def test_policy_request_parse(self):
        req = PolicyRequest.parse_from_dict(
            {
                "request": "smtpd_access_policy",
                "sender": "foo@example.com",
                "recipient": "bar@example.com",
                "sasl_username": "foo@example.com",
            }
        )
        self.assertEqual(req.sender_domain, "example.com")
        self.assertEqual(req.recipient_domain, "example.com")
        self.assertEqual(req.sasl_username, "foo@example.com")


if __name__ == "__main__":
    unittest.main()
