"""调试：通知动作端点 500 复现。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi import HTTPException

from app.api.settings import ExperienceAction, experience_action
from app.core.config import AppConfig
from app.core.database import init_db


class FakeState:
    pass


class FakeApp:
    state = FakeState()


class FakeRequest:
    app = FakeApp()

    def __init__(self, session_factory):
        self.app.state.SessionLocal = session_factory


def main():
    cfg = AppConfig.load()
    engine, S = init_db(cfg.data_dir)
    req = FakeRequest(S)
    try:
        result = experience_action(999, ExperienceAction(action="confirm"), req)
        print("result:", result)
    except HTTPException as e:
        print("HTTPException:", e.status_code, e.detail)
    except Exception:
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
