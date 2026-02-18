"""DISK Diagnostic Tool — Entry Point.

Запуск:
    python run.py

Требуется запуск от имени администратора для доступа к SMART данным дисков.
"""

import sys


def main():
    # Проверяем права администратора перед импортом PySide6
    from disk_diag.utils.admin import is_admin, request_admin_restart

    if not is_admin():
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox

            app = QApplication(sys.argv)
            reply = QMessageBox.question(
                None,
                "Administrator Required",
                "DISK Diagnostic Tool requires administrator privileges\n"
                "to access disk SMART data.\n\n"
                "Restart with elevated privileges?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                request_admin_restart()
            sys.exit(1)

        except ImportError:
            print("ERROR: PySide6 is not installed.")
            print("Run: pip install PySide6")
            sys.exit(1)

    from disk_diag.app import run_application
    run_application()


if __name__ == "__main__":
    main()
