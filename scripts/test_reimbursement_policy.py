import unittest

from app.tools.reimbursement_policy import check_lodging_expense


class ReimbursementPolicyTest(unittest.TestCase):
    def test_check_lodging_expense(self):
        result = check_lodging_expense(
            city = "Shanghai",
            nightly_amount_fen = [49000, 49000]
        )

        self.assertIs(result["within_limit"], True)
        self.assertEqual(result["daily_limit_fen"], 50000)
        self.assertEqual(result["total_amount_fen"], 98000)
        self.assertEqual(result["excess_amount_fen"], 0)
        self.assertEqual(result["rule_id"], "CW-2024-003")


    def test_excess_is_calculated_per_night(self):
        result = check_lodging_expense(
            city="Shanghai",
            nightly_amount_fen=[60000, 30000],
        )

        self.assertIs(result["within_limit"], False)
        self.assertEqual(result["total_amount_fen"], 90000)
        self.assertEqual(result["excess_amount_fen"], 10000)

    def test_invalid_nightly_amounts_are_rejected(self):
        invalid_cases = [
            [],                # 没有费用明细
            [-100],            # 负金额
            [49000, -100],     # 部分明细包含负金额
            [49000.5],         # 金额带小数分
            [True],            # 布尔值
        ]

        for amounts in invalid_cases:
            with self.subTest(amounts=amounts):
                with self.assertRaises(ValueError):
                    check_lodging_expense(
                        city="Shanghai",
                        nightly_amount_fen=amounts,
                    )

    def test_lodging_at_limit_is_accepted(self):
        for city in ("上海", "Shanghai"):
            with self.subTest(city=city):
                result = check_lodging_expense(
                    city=city,
                    nightly_amount_fen=[50000, 50000],
                )

                self.assertIs(result["within_limit"], True)
                self.assertEqual(result["total_amount_fen"], 100000)
                self.assertEqual(result["excess_amount_fen"], 0)

    def test_unconfigured_city_is_rejected(self):
        with self.assertRaises(ValueError):
            check_lodging_expense(
                city="UnknownCity",
                nightly_amount_fen=[49000],
            )