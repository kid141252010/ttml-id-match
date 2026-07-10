import unittest

from ttml_metadata.v2.registry import SourceRegistry


class StubSource:
    def __init__(self, key, dependencies=()):
        self.key = key
        self.dependencies = frozenset(dependencies)


class SourceRegistryTests(unittest.TestCase):
    def test_exposes_stable_dependency_waves_and_lookup(self):
        apple = StubSource("apple_music")
        qq = StubSource("qq_music")
        ncm = StubSource("ncm_music", ("qq_music",))

        registry = SourceRegistry([apple, qq, ncm])

        self.assertIs(registry["qq_music"], qq)
        self.assertEqual(
            [[source.key for source in wave] for wave in registry.dependency_waves],
            [["apple_music", "qq_music"], ["ncm_music"]],
        )

    def test_rejects_duplicates_missing_dependencies_and_cycles(self):
        with self.assertRaisesRegex(ValueError, "unique"):
            SourceRegistry([StubSource("qq"), StubSource("qq")])
        with self.assertRaisesRegex(ValueError, "unknown dependencies"):
            SourceRegistry([StubSource("ncm", ("qq",))])
        with self.assertRaisesRegex(ValueError, "cycle"):
            SourceRegistry([StubSource("a", ("b",)), StubSource("b", ("a",))])


if __name__ == "__main__":
    unittest.main()
