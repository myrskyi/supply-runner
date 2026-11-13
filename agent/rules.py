import re

from . import config

COORDINATOR_FILE = config.RULES_DIR / "coordinator.md"


def versions() -> list[str]:
    found = [p.stem for p in config.RULES_DIR.glob("v*.md") if re.fullmatch(r"v\d+", p.stem)]
    return sorted(found, key=lambda v: int(v[1:]))


def latest() -> str:
    return versions()[-1]


def load(version: str) -> str:
    return (config.RULES_DIR / f"{version}.md").read_text().strip()


def save(version: str, text: str) -> None:
    (config.RULES_DIR / f"{version}.md").write_text(text.strip() + "\n")


def next_version(version: str) -> str:
    return f"v{int(version[1:]) + 1}"


def coordinator_rules() -> list[str]:
    if not COORDINATOR_FILE.exists():
        return []
    return [line[2:].strip() for line in COORDINATOR_FILE.read_text().splitlines() if line.startswith("- ")]


def add_coordinator_rule(text: str) -> None:
    text = " ".join(text.split())
    if text and text not in coordinator_rules():
        with COORDINATOR_FILE.open("a") as f:
            f.write(f"- {text}\n")


def clear_coordinator_rules() -> None:
    COORDINATOR_FILE.unlink(missing_ok=True)
