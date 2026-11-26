import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import UUID

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

    async def test_cancelled_draft_is_not_committed_by_next_write(self):
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "drafts.db"
            store = ReimbursementStore(db_path)
            cancelled_id = UUID(int=1)
            loop = asyncio.get_running_loop()
            first_write: asyncio.Task[str] | None = None

            def cancel_on_insert(sql: str) -> None:
                if first_write is not None and sql.lstrip().startswith(
                    "INSERT INTO reimbursement_tasks"
                ):
                    loop.call_soon_threadsafe(first_write.cancel)

            try:
                await store.init()
                await store.conn.set_trace_callback(cancel_on_insert)

                with patch("app.models.reimbursement.uuid4", return_value=cancelled_id):
                    first_write = asyncio.create_task(
                        store.create_draft(
                            "user-a", "cancelled-thread", {"amount_fen": 98000}
                        )
                    )
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(first_write, timeout=5)

                await store.conn.set_trace_callback(None)
                saved_id = await store.create_draft(
                    "user-b", "next-thread", {"amount_fen": 120000}
                )
            finally:
                await store.close()

            reopened = ReimbursementStore(db_path)
            try:
                await reopened.init()
                self.assertIsNotNone(await reopened.get_draft("user-b", saved_id))
                self.assertIsNone(await reopened.get_draft("user-a", cancelled_id.hex))
            finally:
                await reopened.close()


    async def test_update_draft_increments_revision(self):
        with TemporaryDirectory() as directory:
            store = ReimbursementStore(Path(directory) / "drafts.db")
            original = {"city": "Shanghai", "nights": 2, "amount_fen": 98000}
            changed = {"city": "Shanghai", "nights": 3, "amount_fen": 96000}

            try:
                await store.init()
                task_id = await store.create_draft("user-a", "thread-a", original)

                updated = await store.update_draft(
                    user_id = "user-a",
                    task_id = task_id,
                    draft = changed,
                    expected_revision = 1,
                )

                self.assertIs(updated, True)
                saved = await store.get_draft("user-a", task_id)
                self.assertIsNotNone(saved)
                self.assertEqual(saved["draft"], changed)
                self.assertEqual(saved["draft_revision"], 2)
            finally:
                await store.close()

    async def test_stale_revision_cannot_overwrite_latest_draft(self):
        with TemporaryDirectory() as directory:
            store = ReimbursementStore(Path(directory) / "drafts.db")
            original = {"city": "Shanghai", "nights": 2, "amount_fen": 98000}
            latest = {"city": "Shanghai", "nights": 3, "amount_fen": 96000}
            stale = {"city": "Shanghai", "nights": 2, "amount_fen": 110000}

            try:
                await store.init()
                task_id = await store.create_draft("user-a", "thread-a", original)

                self.assertIs(
                    await store.update_draft("user-a", task_id, latest, expected_revision=1),
                    True,
                )
                self.assertIs(
                    await store.update_draft("user-a", task_id, stale, expected_revision=1),
                    False,
                )

                saved = await store.get_draft("user-a", task_id)
                self.assertIsNotNone(saved)
                self.assertEqual(saved["draft"], latest)
                self.assertEqual(saved["draft_revision"], 2)
            finally:
                await store.close()


    async def test_other_user_cannot_update_draft(self):
        with TemporaryDirectory() as directory:
            store = ReimbursementStore(Path(directory) / "drafts.db")
            original = {"city": "Shanghai", "nights": 2, "amount_fen": 98000}
            changed = {"city": "Shanghai", "nights": 2, "amount_fen": 96000}

            try:
                await store.init()
                task_id = await store.create_draft("user-a", "thread-a", original)

                updated = await store.update_draft(
                    user_id="user-b",
                    task_id=task_id,
                    draft=changed,
                    expected_revision=1,
                )

                self.assertIs(updated, False)
                saved = await store.get_draft("user-a", task_id)
                self.assertIsNotNone(saved)
                self.assertEqual(saved["draft"], original)
                self.assertEqual(saved["draft_revision"], 1)
            finally:
                await store.close()