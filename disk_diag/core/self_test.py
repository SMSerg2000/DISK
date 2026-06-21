"""SMART/NVMe Device Self-test: запуск, опрос прогресса, журнал, отмена.

Первая «активная» диагностика в проекте: программа командует диску провести
внутреннюю самопроверку (short/extended), опрашивает % выполнения и читает
журнал результатов. Self-test **non-destructive** — пользовательские данные не
трогаются (диск гоняет внутренние тесты механики/чтения/NAND).

Транспорты:
  ATA  (SATA / USB-SATA): SMART EXECUTE OFFLINE (0xB0 / feature 0xD4) — запуск;
        SMART READ DATA (0xD0) байт 363 — прогресс; SMART READ LOG (0xD5 @ 0x06)
        — журнал самопроверок.
  NVMe: Device Self-test (Admin opcode 0x14, STC в CDW10) — запуск/отмена;
        Get Log Page 0x06 (564 байта) — прогресс и журнал.

USB-NVMe мосты: запуск self-test через них ненадёжен (vendor-CDB заточены под
чтение лога, не под admin-set) — честно сообщаем, что недоступно.

Движок не держит handle открытым между вызовами: каждая операция открывает свой
короткий RW-handle. Сам тест исполняется в firmware диска, поэтому опрос и даже
закрытие приложения его не прерывают.
"""

import ctypes
import logging
import struct

from .constants import (
    SMART_RCV_DRIVE_DATA, SMART_SEND_DRIVE_COMMAND,
    SMART_EXECUTE_OFFLINE, SMART_READ_LOG, SMART_READ_ATTRIBUTES,
    SMART_LOG_ADDR_SELF_TEST,
    SMART_SELFTEST_SHORT, SMART_SELFTEST_EXTENDED, SMART_SELFTEST_ABORT,
    SMART_SELFTEST_STATUS_OFFSET,
    ProtocolTypeNvme, NVME_LOG_PAGE_SELF_TEST, NVME_ADMIN_DEVICE_SELF_TEST,
    NVME_SELFTEST_SHORT, NVME_SELFTEST_EXTENDED, NVME_SELFTEST_ABORT,
    IOCTL_STORAGE_PROTOCOL_COMMAND,
    IOCTL_STORAGE_QUERY_PROPERTY, StorageDeviceProtocolSpecificProperty,
    PropertyStandardQuery, NVMeDataTypeLogPage,
)
from .structures import SENDCMDOUTPARAMS
from .winapi import DeviceHandle, IoctlFailed, DiskAccessError
from .models import (
    SelfTestType, SelfTestState, SelfTestEntry, SelfTestLog, InterfaceType,
)
from . import smart_ata

logger = logging.getLogger(__name__)

# ATA self-test execution status — high nibble байта статуса (журнал + байт 363)
_ATA_STATUS = {
    0: ("Completed without error", True),
    1: ("Aborted by host", False),
    2: ("Interrupted by host (reset)", False),
    3: ("Fatal or unknown error", False),
    4: ("Completed: unknown failure", False),
    5: ("Completed: electrical failure", False),
    6: ("Completed: servo/seek failure", False),
    7: ("Completed: read failure", False),
    8: ("Completed: handling damage", False),
    15: ("In progress", False),
}

# ATA: имя теста по значению LBA Low, записанному при запуске
_ATA_TEST_NAME = {
    0x01: "Short", 0x02: "Extended", 0x03: "Conveyance", 0x04: "Selective",
    0x81: "Short (captive)", 0x82: "Extended (captive)",
    0x83: "Conveyance (captive)", 0x84: "Selective (captive)",
}

# NVMe self-test result — high nibble байта статуса записи журнала
_NVME_RESULT = {
    0x0: ("Completed without error", True),
    0x1: ("Aborted: host", False),
    0x2: ("Aborted: controller reset", False),
    0x3: ("Aborted: namespace removed", False),
    0x4: ("Aborted: format NVM", False),
    0x5: ("Fatal or unknown error", False),
    0x6: ("Failed: unknown segment", False),
    0x7: ("Failed: one or more segments", False),
    0x8: ("Aborted: unknown reason", False),
    0x9: ("Aborted: sanitize", False),
}

# NVMe: тип теста — low nibble байта статуса (и current operation)
_NVME_TEST_NAME = {0x1: "Short", 0x2: "Extended", 0xE: "Vendor-specific"}


# ============================================================
#  ATA (SATA / USB-SATA)
# ============================================================

def _ata_sat_send(handle, feature, lba_low, data_in):
    """Отправить ATA-команду через SAT — пробуем ATA PT, затем SCSI SAT."""
    last_err = None
    for fn in (smart_ata._sat_smart_command, smart_ata._scsi_sat_smart_command):
        try:
            return fn(handle, feature, data_in=data_in, lba_low=lba_low)
        except (IoctlFailed, DiskAccessError) as e:
            last_err = e
            continue
    if last_err:
        raise last_err
    return b""


def _ata_send_offline(handle, subcommand, use_sat):
    """SMART EXECUTE OFFLINE IMMEDIATE — запустить/прервать self-test (non-data).

    SATA: legacy SMART IOCTL, при отказе → ATA Pass-Through / SCSI SAT (часть
    драйверов не пускает EXECUTE OFFLINE через legacy SMART IOCTL, как и READ LOG).
    """
    if use_sat:
        _ata_sat_send(handle, SMART_EXECUTE_OFFLINE, subcommand, data_in=False)
        return
    try:
        cmd = smart_ata._build_smart_command(
            SMART_EXECUTE_OFFLINE, buffer_size=0, lba_low=subcommand)
        handle.ioctl(SMART_SEND_DRIVE_COMMAND, cmd, ctypes.sizeof(SENDCMDOUTPARAMS))
    except (IoctlFailed, DiskAccessError) as e_legacy:
        try:
            _ata_sat_send(handle, SMART_EXECUTE_OFFLINE, subcommand, data_in=False)
        except (IoctlFailed, DiskAccessError):
            raise e_legacy


def _ata_read_smart_data(handle, use_sat):
    """Прочитать 512-байтный буфер SMART READ DATA (0xD0)."""
    if use_sat:
        return _ata_sat_send(handle, SMART_READ_ATTRIBUTES, 0, data_in=True)
    cmd = smart_ata._build_smart_command(SMART_READ_ATTRIBUTES)
    out = handle.ioctl(SMART_RCV_DRIVE_DATA, cmd, ctypes.sizeof(SENDCMDOUTPARAMS))
    off = SENDCMDOUTPARAMS.bBuffer.offset
    return out[off:off + 512]


def _ata_read_progress(handle, use_sat):
    """Текущий прогресс self-test из SMART READ DATA байт 363 → (running, percent).

    Байт 363: bits 7:4 = статус (0x0F = идёт), bits 3:0 = осталось процентов/10.
    """
    data = _ata_read_smart_data(handle, use_sat)
    if len(data) <= SMART_SELFTEST_STATUS_OFFSET:
        return (False, -1)
    status = data[SMART_SELFTEST_STATUS_OFFSET]
    if (status >> 4) == 0x0F:
        remaining_pct = (status & 0x0F) * 10
        return (True, max(0, 100 - remaining_pct))
    return (False, -1)


def _ata_read_log(handle, log_addr, use_sat):
    """Прочитать 512-байтный ATA log (READ LOG 0xD5 @ log_addr).

    USB → SAT. SATA → legacy SMART IOCTL, при отказе → ATA Pass-Through / SCSI SAT:
    часть драйверов не пускает READ LOG через legacy SMART IOCTL (error 122
    INSUFFICIENT_BUFFER), хотя обычные атрибуты 0xD0 через него читаются.
    """
    if use_sat:
        return _ata_sat_send(handle, SMART_READ_LOG, log_addr, data_in=True)
    try:
        cmd = smart_ata._build_smart_command(
            SMART_READ_LOG, buffer_size=512, lba_low=log_addr)
        out = handle.ioctl(SMART_RCV_DRIVE_DATA, cmd, ctypes.sizeof(SENDCMDOUTPARAMS))
        off = SENDCMDOUTPARAMS.bBuffer.offset
        return out[off:off + 512]
    except (IoctlFailed, DiskAccessError) as e_legacy:
        try:
            return _ata_sat_send(handle, SMART_READ_LOG, log_addr, data_in=True)
        except (IoctlFailed, DiskAccessError):
            raise e_legacy


def _ata_read_selftest_log_raw(handle, use_sat):
    """SMART self-test log (READ LOG 0xD5 @ log 0x06) с fallback на ATA PT/SAT."""
    return _ata_read_log(handle, SMART_LOG_ADDR_SELF_TEST, use_sat)


def _parse_ata_selftest_log(data: bytes) -> list:
    """Распарсить SMART self-test log (512 байт, log 0x06).

    Раскладка: [0..1] revision; 21 дескриптор по 24 байта с offset 2;
    [508] индекс самой свежей записи. Дескриптор: [0] test number,
    [1] status (high nibble), [2..3] POH, [4] checkpoint, [5..8] failing LBA.
    """
    entries = []
    if len(data) < 512:
        return entries
    for i in range(21):
        off = 2 + i * 24
        test_num = data[off]
        if test_num == 0:
            continue  # пустой слот
        code = data[off + 1] >> 4
        poh = struct.unpack_from("<H", data, off + 2)[0]
        failing_lba = struct.unpack_from("<I", data, off + 5)[0]
        status_text, passed = _ATA_STATUS.get(code, (f"Unknown (0x{code:X})", False))
        has_failure = (not passed) and failing_lba not in (0, 0xFFFFFFFF)
        entries.append(SelfTestEntry(
            test_description=_ATA_TEST_NAME.get(test_num, f"0x{test_num:02X}"),
            status_code=code,
            status_text=status_text,
            passed=passed,
            lifetime_hours=poh,
            failing_lba=(failing_lba if has_failure else -1),
        ))
    # Журнал кольцевой; новейшие — с большей наработкой. Для пользователя
    # порядок «свежее сверху» нагляднее точного индекса most-recent.
    entries.sort(key=lambda e: e.lifetime_hours, reverse=True)
    return entries


# ============================================================
#  NVMe
# ============================================================

def _nvme_protocol_command(handle, opcode, cdw10, data_size, nsid=0xFFFFFFFF):
    """Отправить NVMe Admin команду через IOCTL_STORAGE_PROTOCOL_COMMAND.

    data_size=0 → no-data команда (self-test 0x14); >0 → data-in (Get Log Page).
    Возвращает data-буфер (bytes) или b"" для no-data.
    """
    cmd_len = 64        # NVMe SQE
    err_info_len = 64   # NVMe Error Info Log Entry
    header_size = 80 + cmd_len            # 144
    err_info_offset = header_size         # 144
    data_offset = err_info_offset + err_info_len  # 208
    buf_size = data_offset + data_size
    buf = bytearray(buf_size)

    # STORAGE_PROTOCOL_COMMAND header
    struct.pack_into("<I", buf, 0, 1)                  # Version
    struct.pack_into("<I", buf, 4, header_size)        # Length
    struct.pack_into("<I", buf, 8, ProtocolTypeNvme)   # ProtocolType = NVMe
    struct.pack_into("<I", buf, 12, 0)                 # Flags
    struct.pack_into("<I", buf, 24, cmd_len)           # CommandLength
    struct.pack_into("<I", buf, 28, err_info_len)      # ErrorInfoLength
    struct.pack_into("<I", buf, 32, 0)                 # DataToDeviceTransferLength
    struct.pack_into("<I", buf, 36, data_size)         # DataFromDeviceTransferLength
    struct.pack_into("<I", buf, 40, 15)                # TimeOutValue = 15 sec
    struct.pack_into("<I", buf, 44, err_info_offset)   # ErrorInfoOffset
    struct.pack_into("<I", buf, 48, 0)                 # DataToDeviceBufferOffset
    struct.pack_into("<I", buf, 52, data_offset if data_size else 0)  # DataFromDeviceBufferOffset
    struct.pack_into("<I", buf, 56, 1)                 # CommandSpecific = NVMe Admin cmd

    # NVMe Submission Queue Entry
    cmd = 80
    struct.pack_into("<I", buf, cmd + 0, opcode)       # CDW0: opcode
    struct.pack_into("<I", buf, cmd + 4, nsid)         # NSID
    struct.pack_into("<I", buf, cmd + 40, cdw10)       # CDW10

    handle.ioctl_inplace(IOCTL_STORAGE_PROTOCOL_COMMAND, buf)

    ret = struct.unpack_from("<I", buf, 16)[0]         # ReturnStatus
    if ret != 0:
        err = struct.unpack_from("<I", buf, 20)[0]     # ErrorCode
        raise IoctlFailed("NVMe ProtocolCommand", ret,
                          f"ReturnStatus=0x{ret:X}, ErrorCode=0x{err:X}")
    if data_size:
        return bytes(buf[data_offset:data_offset + data_size])
    return b""


def _nvme_get_log_query(handle, lid, log_size):
    """Прочитать NVMe log page через IOCTL_STORAGE_QUERY_PROPERTY.

    Тот же механизм, что и для health — работает на драйверах, которые отвергают
    IOCTL_STORAGE_PROTOCOL_COMMAND с error 87 (Microsoft StorNVMe / RAID / VMD).
    Только для ЧТЕНИЯ логов. Перебираем размер STORAGE_PROTOCOL_SPECIFIC_DATA
    (по версии Windows: 40/44/28 байт).
    """
    HEADER = 8  # STORAGE_PROPERTY_QUERY: PropertyId(4) + QueryType(4)
    last_err = None
    for proto_size in (40, 44, 28):
        buf = bytearray(HEADER + proto_size + log_size)
        struct.pack_into("<I", buf, 0, StorageDeviceProtocolSpecificProperty)
        struct.pack_into("<I", buf, 4, PropertyStandardQuery)
        # STORAGE_PROTOCOL_SPECIFIC_DATA @ HEADER
        struct.pack_into("<I", buf, HEADER + 0, ProtocolTypeNvme)
        struct.pack_into("<I", buf, HEADER + 4, NVMeDataTypeLogPage)
        struct.pack_into("<I", buf, HEADER + 8, lid)          # ProtocolDataRequestValue = LID
        struct.pack_into("<I", buf, HEADER + 12, 0)           # SubValue
        struct.pack_into("<I", buf, HEADER + 16, proto_size)  # ProtocolDataOffset
        struct.pack_into("<I", buf, HEADER + 20, log_size)    # ProtocolDataLength
        try:
            handle.ioctl_inplace(IOCTL_STORAGE_QUERY_PROPERTY, buf)
        except (IoctlFailed, DiskAccessError) as e:
            last_err = e
            continue
        resp_off = struct.unpack_from("<I", buf, HEADER + 16)[0]
        resp_len = struct.unpack_from("<I", buf, HEADER + 20)[0]
        if resp_len == 0 or resp_len > log_size + 64:
            last_err = IoctlFailed("NVMe GetLog QueryProperty", 0,
                                   f"bad ProtocolDataLength={resp_len} (proto={proto_size})")
            continue
        start = HEADER + resp_off
        end = min(start + resp_len, len(buf))
        return bytes(buf[start:end])
    raise last_err or IoctlFailed("NVMe GetLog QueryProperty", 0, "no proto size worked")


def _nvme_get_log(handle, lid, log_size):
    """Прочитать NVMe log page: сначала QueryProperty (совместимее), при отказе —
    ProtocolCommand. Возвращает raw-байты лога."""
    try:
        return _nvme_get_log_query(handle, lid, log_size)
    except (IoctlFailed, DiskAccessError) as e_query:
        try:
            numd = (log_size // 4) - 1
            cdw10 = (numd << 16) | lid
            return _nvme_protocol_command(handle, 0x02, cdw10, log_size)
        except (IoctlFailed, DiskAccessError):
            raise e_query  # исходная (QueryProperty) ошибка информативнее


def _nvme_start_self_test(handle, stc):
    """Запустить/прервать NVMe Device Self-test (Admin 0x14, STC в CDW10).

    SET-команда — только через ProtocolCommand (QueryProperty умеет лишь чтение).
    На драйверах без поддержки STORAGE_PROTOCOL_COMMAND запуск недоступен.
    """
    _nvme_protocol_command(handle, NVME_ADMIN_DEVICE_SELF_TEST, stc, data_size=0)


def _nvme_read_selftest_log_raw(handle):
    """Get Log Page 0x06 (Device Self-test log, 564 байта) — через QueryProperty."""
    return _nvme_get_log(handle, NVME_LOG_PAGE_SELF_TEST, 564)


def _parse_nvme_selftest_log(data: bytes):
    """Распарсить NVMe Device Self-test log (564 байта) → (entries, state).

    [0] current operation (0=нет, иначе идёт), [1] % complete (bits 6:0).
    20 записей по 28 байт с offset 4. Запись: [0] status (high=result,
    low=type), [2] valid diag info, [4..11] POH, [16..23] failing LBA.
    """
    state = SelfTestState()
    entries = []
    if len(data) < 564:
        return entries, state

    if data[0] != 0:
        state.running = True
        state.percent = data[1] & 0x7F

    for i in range(20):
        off = 4 + i * 28
        status_byte = data[off]
        result = status_byte & 0x0F   # bits 3:0 = Self-test Result
        test_type = status_byte >> 4  # bits 7:4 = Self Test Code (тип теста)
        if result == 0xF:
            continue  # запись не используется (по спеке)
        poh = struct.unpack_from("<Q", data, off + 4)[0]
        valid = data[off + 2]
        failing_lba = struct.unpack_from("<Q", data, off + 16)[0]
        # Защита от дисков, заполняющих пустые слоты нулями вместо 0xF:
        # полностью нулевая запись (нет результата/наработки/флагов) — пустая.
        if status_byte == 0 and poh == 0 and valid == 0:
            continue
        status_text, passed = _NVME_RESULT.get(result, (f"Unknown (0x{result:X})", False))
        # Valid Diagnostic Information (байт +2): bit1 (0x02) = Failing LBA Valid
        # (bit0 = NSID Valid — НЕ путать).
        entries.append(SelfTestEntry(
            test_description=_NVME_TEST_NAME.get(test_type, f"0x{test_type:X}"),
            status_code=result,
            status_text=status_text,
            passed=passed,
            lifetime_hours=(poh if 0 < poh < (1 << 63) else -1),
            failing_lba=(failing_lba if (valid & 0x02) else -1),
        ))
    # NVMe-журнал уже упорядочен: запись 0 — самая свежая.
    return entries, state


# ============================================================
#  Высокоуровневый движок
# ============================================================

class SelfTestEngine:
    """Выбор транспорта по интерфейсу + операции start/poll/abort/read_log.

    interface_type — строка InterfaceType.value ("NVMe"/"SATA"/"USB"/...).
    """

    def __init__(self, drive_number: int, interface_type: str,
                 model: str = "", serial: str = ""):
        self.drive_number = drive_number
        self.interface = interface_type
        self.model = model
        self.serial = serial
        self._is_nvme = (interface_type == InterfaceType.NVME.value)
        # USB трактуем как USB-SATA (SAT pass-through). USB-NVMe мост упадёт
        # на старте/опросе → честный note в read_log.
        self._use_sat = (interface_type == InterfaceType.USB.value)

    @property
    def is_nvme(self) -> bool:
        return self._is_nvme

    def start(self, test_type: SelfTestType) -> None:
        """Запустить self-test. Бросает IoctlFailed/DiskAccessError при отказе."""
        with DeviceHandle(self.drive_number, read_only=False) as h:
            if self._is_nvme:
                stc = (NVME_SELFTEST_SHORT if test_type == SelfTestType.SHORT
                       else NVME_SELFTEST_EXTENDED)
                try:
                    _nvme_start_self_test(h, stc)
                except (IoctlFailed, DiskAccessError) as e:
                    # Запуск — единственная SET-команда; она требует
                    # STORAGE_PROTOCOL_COMMAND. Если драйвер его не пускает
                    # (Microsoft StorNVMe / RAID / VMD, error 87), запуск
                    # невозможен — но ЧТЕНИЕ журнала/прогресса работает через
                    # QueryProperty. Сообщаем человеку понятно.
                    raise IoctlFailed(
                        "NVMe self-test start", 0,
                        "this driver does not allow starting an NVMe self-test "
                        "(STORAGE_PROTOCOL_COMMAND unavailable). Reading the "
                        f"self-test/error logs still works. [{e}]") from e
            else:
                sub = (SMART_SELFTEST_SHORT if test_type == SelfTestType.SHORT
                       else SMART_SELFTEST_EXTENDED)
                _ata_send_offline(h, sub, self._use_sat)
        logger.info(f"Self-test {test_type.value} started on drive "
                    f"{self.drive_number} ({self.interface})")

    def abort(self) -> None:
        """Прервать текущий self-test (best-effort)."""
        with DeviceHandle(self.drive_number, read_only=False) as h:
            if self._is_nvme:
                _nvme_start_self_test(h, NVME_SELFTEST_ABORT)
            else:
                _ata_send_offline(h, SMART_SELFTEST_ABORT, self._use_sat)
        logger.info(f"Self-test abort sent to drive {self.drive_number}")

    def poll(self) -> SelfTestState:
        """Опросить прогресс. Бросает при потере связи с диском."""
        with DeviceHandle(self.drive_number, read_only=False) as h:
            if self._is_nvme:
                _, state = _parse_nvme_selftest_log(_nvme_read_selftest_log_raw(h))
                return state
            running, percent = _ata_read_progress(h, self._use_sat)
            return SelfTestState(running=running, percent=percent)

    def read_log(self) -> SelfTestLog:
        """Прочитать журнал самопроверок. Не бросает — возвращает supported=False."""
        try:
            with DeviceHandle(self.drive_number, read_only=False) as h:
                if self._is_nvme:
                    entries, state = _parse_nvme_selftest_log(
                        _nvme_read_selftest_log_raw(h))
                    return SelfTestLog(entries=entries, state=state)
                raw = _ata_read_selftest_log_raw(h, self._use_sat)
                return SelfTestLog(entries=_parse_ata_selftest_log(raw))
        except (IoctlFailed, DiskAccessError) as e:
            logger.info(f"Self-test log unavailable on drive {self.drive_number}: {e}")
            return SelfTestLog(supported=False, note=self._unsupported_note(str(e)))

    def _unsupported_note(self, err: str) -> str:
        if self._use_sat:
            return ("Self-test недоступен через этот USB-мост "
                    "(возможно, это USB-NVMe). Подключите диск напрямую (SATA/M.2).")
        return f"Self-test не поддерживается этим диском или драйвером ({err})."
