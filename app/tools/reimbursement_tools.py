from app.models.reimbursement import ReimbursementStore
from app.tools.reimbursement_policy import check_lodging_expense
async def create_reimbursement_draft(
        store: ReimbursementStore,
        user_id: str,
        thread_id: str,
        city: str,
        nightly_amount_fen: list[int],
) -> dict:
    """校验住宿费并保存报销草稿"""
    policy_check =  check_lodging_expense(city, nightly_amount_fen)

    draft = {
        "city": city,
        "nightly_amount_fen": list(nightly_amount_fen),
        "nights": len(nightly_amount_fen),
        "amount_fen": policy_check["total_amount_fen"],
        "policy_check": policy_check,
    }

    task_id = await store.create_draft(user_id, thread_id, draft)

    return {
        "task_id": task_id,
        "draft": draft,
    }