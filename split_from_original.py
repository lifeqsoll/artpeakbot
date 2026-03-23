from __future__ import annotations

from pathlib import Path


ORIGINAL_PATH = Path(r"C:\Users\mqqsol\Downloads\artpeakbot (2).py")
OUT_DIR = Path(r"C:\projects\artpeakbot")


MARK_LOGIC = "ОСНОВНЫЕ ОБРАБОТЧИКИ"
MARK_RUN = "ЗАПУСК БОТА"


def _read_lines(path: Path) -> list[str]:
    for enc in ("utf-8", "utf-8-sig", "cp1251", "cp866"):
        try:
            return path.read_text(encoding=enc).splitlines(keepends=True)
        except UnicodeDecodeError:
            continue
    # fallback: хотя маркеры могут не совпасть, зато код хотя бы прочитается
    return path.read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True)


def _find_marker_index(lines: list[str], marker: str) -> int:
    for i, line in enumerate(lines):
        if marker in line:
            return i
    raise RuntimeError(f"Marker not found: {marker}")


if __name__ == "__main__":
    if not ORIGINAL_PATH.exists():
        raise SystemExit(f"Original file not found: {ORIGINAL_PATH}")

    lines = _read_lines(ORIGINAL_PATH)

    idx_logic = _find_marker_index(lines, MARK_LOGIC)
    idx_run = _find_marker_index(lines, MARK_RUN)

    # bot_logic.py = everything before "ОСНОВНЫЕ ОБРАБОТЧИКИ"
    logic_lines = lines[:idx_logic]

    # bot_handlers.py = from "ОСНОВНЫЕ ОБРАБОТЧИКИ" up to (but excluding) "ЗАПУСК БОТА"
    handlers_lines = lines[idx_logic:idx_run]

    (OUT_DIR / "bot_logic.py").write_text(
        "".join(logic_lines),
        encoding="utf-8",
    )

    header = "from .bot_logic import *\n\n"
    (OUT_DIR / "bot_handlers.py").write_text(
        header + "".join(handlers_lines),
        encoding="utf-8",
    )

    print("Split completed:")
    print(f"- bot_logic.py lines: {len(logic_lines)}")
    print(f"- bot_handlers.py lines: {len(handlers_lines)}")

