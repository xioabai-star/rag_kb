def check_lodging_expense(city: str, nightly_amount_fen: list[int]) -> dict:
    """根据城市和每晚费用检查住宿费限额"""
    first_tier_cities = {
        "北京", "上海", "广州", "深圳",
        "Beijing", "Shanghai", "Guangzhou", "Shenzhen",
    }
    if city not in first_tier_cities:
        raise ValueError(f"该城市的住宿费标准待核对：{city}")

    if not nightly_amount_fen:
        raise ValueError("至少需要一晚费用")

    for amount in nightly_amount_fen:
        if type(amount) is not int or amount < 0:
            raise ValueError("每晚费用必须是非负整数分")
    daily_limit_fen = 50000
    total_amount_fen = sum(nightly_amount_fen)
    excess_amount_fen = 0

    for amount in nightly_amount_fen:
        if amount > daily_limit_fen:
            excess_amount_fen += amount - daily_limit_fen

    return{
        "within_limit": excess_amount_fen == 0,
        "daily_limit_fen": daily_limit_fen,
        "total_amount_fen": total_amount_fen,
        "excess_amount_fen": excess_amount_fen,
        "rule_id": "CW-2024-003",
    }
