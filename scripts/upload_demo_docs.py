"""验证辅助：批量上传 sample_docs/ 并轮询状态（UTF-8 文件名可靠）。

用法：uv run python scripts/upload_demo_docs.py
"""

import sys
import time
from pathlib import Path

import requests

BASE = "http://127.0.0.1:8002/api/v1"
SAMPLE_DIR = Path(__file__).resolve().parents[1] / "sample_docs"

FILES = [
    "员工手册.md",
    "信息安全管理制度.md",
    "制度与流程 FAQ.md",
    "考勤管理制度.pdf",
    "请假管理制度.pdf",
    "费用报销制度.pdf",
    "差旅报销细则.pdf",
    "发票管理办法.pdf",
    "薪酬保密制度.pdf",
    "产品操作指南.docx",
]

DEMO_USER = "admin"    # 演示账号；不存在时脚本自动注册（全新环境也能跑通）
DEMO_PASS = "admin123"

# 带持久 header 的客户端：登录一次，后续请求自动带 token
_sess = requests.Session()


def login() -> None:
    """模拟前端登录：401（账号不存在）→ 注册 → 再登录。"""
    resp = _sess.post(
        f"{BASE}/auth/login",
        data={"username": DEMO_USER, "password": DEMO_PASS},  # data= 发表单（login 是 OAuth2 表单接口）
    )
    if resp.status_code == 401:
        _sess.post(
            f"{BASE}/auth/register",
            json={"username": DEMO_USER, "password": DEMO_PASS, "role": "admin"},
        )
        resp = _sess.post(
            f"{BASE}/auth/login",
            data={"username": DEMO_USER, "password": DEMO_PASS},
        )
    resp.raise_for_status()
    _sess.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"
    print(f"已登录演示账号: {DEMO_USER}")


def main() -> None:
    login()  # 先模拟前端登录，否则后续请求全部 401

    for name in FILES:
        path = SAMPLE_DIR / name
        if not path.exists():
            print(f"跳过（不存在）: {name}")
            continue
        with open(path, "rb") as f:
            resp = _sess.post(
                f"{BASE}/documents/upload",
                files={"file": (name, f)},
                timeout=60,
            )
        data = resp.json()
        print(f"上传 {name}: {data['doc_id']} -> {data['status']}")

    # 轮询直到全部稳定（ready/failed）
    for _ in range(40):
        docs = _sess.get(f"{BASE}/documents", timeout=15).json()["documents"]
        active = [d for d in docs if d["status"] == "processing"]
        if not active:
            break
        time.sleep(3)

    print("\n===== 最终状态 =====")
    for d in _sess.get(f"{BASE}/documents", timeout=15).json()["documents"]:
        print(f"{d['doc_id']} | {d['status']} | 块数: {d['chunk_count']} | {d['error'] or ''}")


if __name__ == "__main__":
    sys.exit(main())
