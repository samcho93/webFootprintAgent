"""프로젝트 루트의 .env 를 환경변수로 읽는다 (이미 설정된 값은 덮어쓰지 않음)."""
import os

ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")


def load_env(path: str = ENV_PATH) -> bool:
    if not os.path.exists(path):
        return False
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and v and not os.environ.get(k):
                os.environ[k] = v
    return True
