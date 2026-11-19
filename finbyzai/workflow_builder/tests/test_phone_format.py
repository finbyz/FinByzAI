"""WF-42: the phone transform returns a readable international number, Switzerland by default."""
import unittest

from finbyzai.workflow_builder.engine import _transform_output


def fmt(value, **config):
	return _transform_output({"operation": "format_phone", **config}, [value], seed="test")


class TestPhoneFormat(unittest.TestCase):
	def test_swiss_mobile_without_country_code(self):
		self.assertEqual(fmt("0791234567"), "+41 79 123 45 67")
		self.assertEqual(fmt("079 123 45 67"), "+41 79 123 45 67")

	def test_double_zero_and_plus_prefixes(self):
		self.assertEqual(fmt("0041791234567"), "+41 79 123 45 67")
		self.assertEqual(fmt("+4930123456"), "+49 30 123456")

	def test_other_default_country_and_e164_option(self):
		self.assertEqual(fmt("0301234567", country_code="49"), "+49 30 1234567")
		self.assertEqual(fmt("0791234567", phone_format="e164"), "+41791234567")
