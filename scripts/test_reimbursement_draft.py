import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.models.reimbursement import ReimbursementStore


class ReimbursementDraftTest(unittest.IsolatedAsyncioTestCase):
    async def test_only_owner_can_read_draft_after_reopen(self):
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "drafts.db"
            draft = {"city": "Shanghai", "nights": 2, "amount_fen": 98000}

            store = ReimbursementStore(db_path)
            try:
                await store.init()
                task_id = await store.create_draft("user-a", "thread-a", draft)
            finally:
                await store.close()

            reopened = ReimbursementStore(db_path)
            try:
                await reopened.init()
                saved = await reopened.get_draft(user_id="user-a", task_id=task_id)
                self.assertEqual(saved, {
                    "task_id": task_id,
                    "thread_id": "thread-a",
                    "status": "collecting_info",
                    "draft": draft,
                    "draft_revision": 1,
                    "reimbursement_id": None,
                })
                self.assertIsNone(
                    await reopened.get_draft(user_id="user-b", task_id=task_id)
                )
            finally:
                await reopened.close()