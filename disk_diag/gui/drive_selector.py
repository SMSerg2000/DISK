"""ComboBox для выбора физического диска."""

from PySide6.QtWidgets import QWidget, QHBoxLayout, QComboBox, QPushButton
from PySide6.QtCore import Signal

from ..core.models import DriveInfo
from ..i18n import tr


class DriveSelector(QWidget):
    """Панель выбора диска: ComboBox + кнопка Refresh."""

    drive_selected = Signal(int)  # Индекс в списке дисков
    refresh_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drives: list[DriveInfo] = []

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._combo = QComboBox()
        self._combo.setMinimumWidth(500)
        self._combo.currentIndexChanged.connect(self._on_index_changed)

        self._refresh_btn = QPushButton(tr("Refresh", "Обновить"))
        self._refresh_btn.setFixedWidth(90)
        self._refresh_btn.clicked.connect(self.refresh_requested.emit)

        layout.addWidget(self._combo, stretch=1)
        layout.addWidget(self._refresh_btn)

    def set_drives(self, drives: list[DriveInfo]):
        """Заполнить ComboBox списком дисков."""
        self._drives = drives
        self._combo.blockSignals(True)
        self._combo.clear()
        for drive in drives:
            self._combo.addItem(drive.display_name)
        self._combo.blockSignals(False)

        if drives:
            self._combo.setCurrentIndex(0)
            self.drive_selected.emit(0)

    def get_selected_drive(self) -> DriveInfo | None:
        """Вернуть текущий выбранный DriveInfo."""
        idx = self._combo.currentIndex()
        if 0 <= idx < len(self._drives):
            return self._drives[idx]
        return None

    def _on_index_changed(self, index: int):
        if 0 <= index < len(self._drives):
            self.drive_selected.emit(index)
