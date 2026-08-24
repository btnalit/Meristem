import unittest

from meristem.llm import parse_file_map


class ParseFileMapTests(unittest.TestCase):
    def test_accepts_bare_json_object(self):
        self.assertEqual(parse_file_map('{"seed/x.py":"print(1)"}'),
                         {"seed/x.py": "print(1)"})

    def test_accepts_complete_json_code_fence(self):
        self.assertEqual(parse_file_map('```json\n{"seed/x.py":"print(1)"}\n```'),
                         {"seed/x.py": "print(1)"})

    def test_rejects_explanatory_text_around_json(self):
        self.assertEqual(parse_file_map('Here is the change:\n{"seed/x.py":"print(1)"}'), {})

    def test_rejects_non_json_fence(self):
        self.assertEqual(parse_file_map('```python\n{"seed/x.py":"print(1)"}\n```'), {})


if __name__ == "__main__":
    unittest.main()
