import unittest

import ttml_metadata


class V2PublicApiTests(unittest.TestCase):
    def test_package_root_exposes_only_the_v2_domain_and_entry_point(self):
        self.assertEqual(
            set(ttml_metadata.__all__),
            {
                "Candidate",
                "ChangePlan",
                "MatchingApplication",
                "MatchingEngine",
                "PairSnapshot",
                "PairingPlan",
                "Selection",
                "SourceAdapter",
                "SourceRegistry",
                "SourceResult",
                "TtmlPlanner",
                "TtmlWriter",
                "build_pairing_plan",
                "main",
            },
        )
        self.assertFalse(hasattr(ttml_metadata, "PairMetadata"))
        self.assertFalse(hasattr(ttml_metadata, "confirm_qq_music_candidates"))
        self.assertFalse(hasattr(ttml_metadata, "_process_prepared_pair"))


if __name__ == "__main__":
    unittest.main()
