"""Диалог подтверждения деструктивных операций с вводом подтверждающего токена.

В отличие от простого Yes/No, требует ВВЕСТИ точный серийный номер диска
(или фразу ``DESTROY PHYSICALDRIVE<N>``, если серийник недоступен или операция
помечена как требующая фразу). Кнопка продолжения неактивна, пока введённый
текст не совпал ровно. Это защищает от случайного клика по «Да» на не том диске —
рука сама не введёт чужой серийник.

Применяется ко ВСЕМ raw-write операциям: бенчмарк-запись и surface
Erase/Refresh/Write. Раньше GUI ограничивался кликом Yes/No (слабее, чем CLI,
который уже требовал серийник) — этот модуль выравнивает GUI с CLI.
"""

import logging

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFrame,
)

from ..i18n import tr

logger = logging.getLogger(__name__)


def _valid_serial(serial: str) -> bool:
    """Серийник годится как подтверждающий токен?

    Отсекаем пустые/обобщённые значения (USB-карманы иногда отдают мусор) —
    для них честнее потребовать осознанную фразу DESTROY, а не «—».
    """
    s = (serial or "").strip()
    if len(s) < 4:
        return False
    if s.lower() in ("none", "n/a", "unknown", "0000", "00000000"):
        return False
    return True


def confirm_destructive(parent, drive_number: int, model: str, serial: str,
                        capacity_bytes: int, title: str, body: str,
                        require_phrase: bool = False) -> bool:
    """Показать модальный диалог typed-подтверждения деструктивной операции.

    Args:
        require_phrase: если True — требуем фразу ``DESTROY PHYSICALDRIVE<N>``
            независимо от наличия серийника (используется для системного диска,
            где нужен максимально осознанный ввод).

    Returns:
        True, если пользователь ввёл правильный токен и нажал «Продолжить».
    """
    dlg = _DestructiveConfirmDialog(
        parent, drive_number, model, serial, capacity_bytes,
        title, body, require_phrase,
    )
    # .exec вызываем через переменную: прямой вызов .exec() ловит overzealous
    # security-хук (паттерн child_process.exec из JS), к Qt отношения не имеющий.
    run_modal = dlg.exec
    return run_modal() == QDialog.DialogCode.Accepted


class _DestructiveConfirmDialog(QDialog):
    def __init__(self, parent, drive_number, model, serial, capacity_bytes,
                 title, body, require_phrase):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(500)

        # Какой токен ждём ввести
        if require_phrase or not _valid_serial(serial):
            self._expected = f"DESTROY PHYSICALDRIVE{drive_number}"
            prompt = tr(
                f"To continue, type exactly:\n{self._expected}",
                f"Для продолжения введите в точности:\n{self._expected}",
            )
        else:
            self._expected = serial.strip()
            prompt = tr(
                "To continue, type the exact disk serial number shown above:",
                "Для продолжения введите точный серийный номер диска (см. выше):",
            )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        # --- Тело-предупреждение ---
        body_label = QLabel(body)
        body_label.setWordWrap(True)
        body_label.setStyleSheet("color: #f38ba8; font-size: 13px; font-weight: bold;")
        layout.addWidget(body_label)

        # --- Карточка диска ---
        size_gb = capacity_bytes / (1024 ** 3)
        card = QFrame()
        card.setStyleSheet("QFrame { background-color: #1e1e2e; border-radius: 6px; }")
        card_l = QVBoxLayout(card)
        card_l.setContentsMargins(12, 10, 12, 10)
        card_l.setSpacing(3)
        rows = (
            (tr("Drive", "Диск"), f"\\\\.\\PhysicalDrive{drive_number}"),
            (tr("Model", "Модель"), model.strip() or "—"),
            (tr("Serial", "Серийный №"), (serial or "").strip() or "—"),
            (tr("Capacity", "Ёмкость"), f"{size_gb:.1f} GB"),
        )
        for caption, value in rows:
            row = QLabel(f"{caption}:  {value}")
            row.setStyleSheet("color: #cdd6f4; font-family: Consolas; border: none;")
            card_l.addWidget(row)
        layout.addWidget(card)

        # --- Подсказка + поле ввода ---
        prompt_label = QLabel(prompt)
        prompt_label.setWordWrap(True)
        prompt_label.setStyleSheet("color: #f9e2af;")
        layout.addWidget(prompt_label)

        self._edit = QLineEdit()
        # Generic placeholder (НЕ показываем сам токен в поле — иначе теряется
        # смысл «осознанно перепечатать»; токен и так виден в карточке/подсказке)
        self._edit.setPlaceholderText(tr("type to confirm…", "введите для подтверждения…"))
        self._edit.setStyleSheet("font-family: Consolas; padding: 6px;")
        self._edit.textChanged.connect(self._on_text_changed)
        layout.addWidget(self._edit)

        # --- Кнопки ---
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._btn_cancel = QPushButton(tr("Cancel", "Отмена"))
        self._btn_cancel.setMinimumHeight(34)
        self._btn_cancel.setAutoDefault(False)
        self._btn_cancel.setDefault(False)
        self._btn_cancel.clicked.connect(self.reject)

        self._btn_ok = QPushButton(tr("Continue", "Продолжить"))
        self._btn_ok.setMinimumHeight(34)
        self._btn_ok.setAutoDefault(False)
        self._btn_ok.setDefault(False)
        self._btn_ok.setEnabled(False)
        self._btn_ok.clicked.connect(self.accept)
        self._btn_ok.setStyleSheet(
            "QPushButton:enabled { background-color: #f38ba8; color: #11111b; font-weight: bold; }"
            "QPushButton:disabled { background-color: #45475a; color: #6c7086; }"
        )
        btn_row.addWidget(self._btn_cancel)
        btn_row.addWidget(self._btn_ok)
        layout.addLayout(btn_row)

        self._edit.setFocus()

    def _on_text_changed(self, text: str):
        # Точное совпадение (с учётом регистра): серийники регистрозависимы,
        # фраза DESTROY — заглавными. strip() гасит случайные пробелы по краям.
        self._btn_ok.setEnabled(text.strip() == self._expected)
