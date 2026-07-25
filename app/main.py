"""Entry point for Bash Editor."""

from __future__ import annotations

import sys
from pathlib import Path

# Allow `python app/main.py` from a checkout without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.glview import configure_default_format  # noqa: E402
from app.window import MainWindow  # noqa: E402


def main() -> int:
    # The surface format has to be set before the first window exists.
    configure_default_format()
    app = QApplication(sys.argv)
    app.setApplicationName("Bash Editor")

    window = MainWindow()
    window.show()

    if len(sys.argv) > 1:
        candidate = Path(sys.argv[1])
        if candidate.is_file():
            window.load_archive(candidate)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
