from __future__ import annotations

import unittest

from core.taxonomy import classify_etf, taxonomy_profile_to_dict


class ETFTaxonomyTests(unittest.TestCase):
    def test_classifies_core_broad_index(self) -> None:
        profile = classify_etf({"code": "510300.SH", "name": "华泰柏瑞沪深300ETF", "category_key": "沪深300"})

        self.assertEqual(profile.etf_type, "broad_index_core")
        self.assertEqual(profile.subtype, "core_beta")
        self.assertEqual(profile.legacy_valuation_model_type, "broad_index")
        self.assertGreater(profile.classification_confidence, 0.85)
        self.assertTrue(profile.classification_reasons)

    def test_classifies_growth_broad_index(self) -> None:
        profile = classify_etf({"code": "159915.SZ", "name": "易方达创业板ETF", "theme": "创业板宽基"})

        self.assertEqual(profile.etf_type, "broad_index_growth")
        self.assertEqual(profile.legacy_valuation_model_type, "broad_index")

    def test_classifies_factor_strategy(self) -> None:
        profile = classify_etf({"code": "512890.SH", "name": "华泰柏瑞中证红利低波动ETF"})

        self.assertEqual(profile.etf_type, "factor_strategy")
        self.assertEqual(profile.subtype, "dividend_low_vol")
        self.assertEqual(profile.legacy_valuation_model_type, "factor_defensive")

    def test_classifies_cash_equivalent_before_bond(self) -> None:
        profile = classify_etf({"code": "511360.SH", "name": "短融ETF", "tracking_index": "短融债指数"})

        self.assertEqual(profile.etf_type, "cash_equivalent")
        self.assertEqual(profile.legacy_valuation_model_type, "cash_like")

    def test_classifies_theme_lifecycle_with_stage(self) -> None:
        profile = classify_etf(
            {
                "code": "588170.SH",
                "name": "科创半导体材料设备ETF",
                "theme": "硬科技电子/半导体",
                "source_path": "result.theme_ranking.top_etf",
                "score": 96.0,
            }
        )

        self.assertEqual(profile.etf_type, "theme_lifecycle")
        self.assertEqual(profile.lifecycle_stage, "crowded")

    def test_classification_is_deterministic_and_report_ready(self) -> None:
        item = {"code": "518880.SH", "name": "华安黄金ETF", "theme": "黄金"}
        first = taxonomy_profile_to_dict(classify_etf(item))
        second = taxonomy_profile_to_dict(classify_etf(item))

        self.assertEqual(first, second)
        self.assertEqual(first["etf_type"], "commodity_etf")


if __name__ == "__main__":
    unittest.main()
