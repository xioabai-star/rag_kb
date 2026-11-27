import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.models.reimbursement import ReimbursementStore
from app.tools.reimbursement_tools import create_reimbursement_draft

from unittest.mock import patch
from uuid import UUID

class ReimbursementToolsTest(unittest.IsolatedAsyncioTestCase):
    async def test_create_draft_persists_policy_check(self):
        with TemporaryDirectory() as directory:
            store = ReimbursementStore(Path(directory) / "drafts.db")
            try:
                await store.init()
                result = await create_reimbursement_draft(
                    store=store,
                    user_id="user-a",
                    thread_id="thread-a",
                    city="Shanghai",
                    nightly_amount_fen=[49000, 49000],
                )

                saved = await store.get_draft("user-a", result["task_id"])
                self.assertIsNotNone(saved)
                self.assertEqual(saved["draft"]["amount_fen"], 98000)
                self.assertIs(saved["draft"]["policy_check"]["within_limit"], True)
                self.assertEqual(
                    saved["draft"]["policy_check"]["rule_id"], "CW-2024-003"
                )
                self.assertEqual(result["draft"], saved["draft"])
            finally:
                await store.close()


    async def test_over_limit_draft_keeps_policy_result(self):
        with TemporaryDirectory() as directory:
            store = ReimbursementStore(Path(directory) / "drafts.db")
            try:
                await store.init()
                result = await create_reimbursement_draft(
                    store=store,
                    user_id="user-a",
                    thread_id="thread-a",
                    city="Shanghai",
                    nightly_amount_fen=[60000, 30000],
                )

                saved = await store.get_draft("user-a", result["task_id"])
                self.assertIsNotNone(saved)
                self.assertEqual(saved["draft"]["amount_fen"], 90000)
                self.assertIs(saved["draft"]["policy_check"]["within_limit"], False)
                self.assertEqual(
                    saved["draft"]["policy_check"]["excess_amount_fen"], 10000
                )
            finally:
                await store.close()

    async def test_invalid_input_does_not_create_draft(self):
        with TemporaryDirectory() as directory:
            store = ReimbursementStore(Path(directory) / "drafts.db")
            fixed_id = UUID(int=1)
            try:
                await store.init()

                with patch("app.models.reimbursement.uuid4", return_value=fixed_id):
                    with self.assertRaises(ValueError):
                        await create_reimbursement_draft(
                            store=store,
                            user_id="user-a",
                            thread_id="thread-a",
                            city="Shanghai",
                            nightly_amount_fen=[-100],
                        )

                saved = await store.get_draft("user-a", fixed_id.hex)
                self.assertIsNone(saved)
            finally:
                await store.close()