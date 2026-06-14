import os
from pathlib import Path
from typing import Dict, Iterable, Optional


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def parse_env_lines(lines: Iterable[str]) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lstrip("\ufeff")
        if not key:
            continue
        values[key] = _strip_quotes(value.strip())
    return values


def find_env_file(start_dir: Optional[Path] = None) -> Optional[Path]:
    explicit = os.environ.get("WECHAT_REMIND_ENV_FILE")
    if explicit:
        path = Path(explicit)
        return path if path.exists() else None

    current = (start_dir or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        candidate = directory / ".env"
        if candidate.exists():
            return candidate
    return None


def load_env_file(path: Optional[Path] = None, override: bool = False) -> Optional[Path]:
    env_path = path or find_env_file()
    if env_path is None:
        return None

    env_dir = str(env_path.resolve().parent)
    if override or "WECHAT_REMIND_ENV_DIR" not in os.environ:
        os.environ["WECHAT_REMIND_ENV_DIR"] = env_dir

    values = parse_env_lines(env_path.read_text(encoding="utf-8").splitlines())
    for key, value in values.items():
        if override or key not in os.environ:
            os.environ[key] = value
    return env_path
