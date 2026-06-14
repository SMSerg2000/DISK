# DISK Diagnostic Tool — Developer Guide

## 1. Architecture Overview

### Package Structure

```
disk_diag/
├── __init__.py          # Version (__version__), app name
├── app.py               # QApplication, Fusion style, theme + logging setup
├── i18n.py              # Localization: tr("en", "ru"), lang.cfg
├── core/                # Backend (no GUI dependencies)
│   ├── constants.py     # IOCTL codes, bus types, Windows API constants
│   ├── structures.py    # ctypes Structures (SMART, Storage API, SCSI/ATA PT)
│   ├── winapi.py        # CreateFile, DeviceIoControl, ReadFile, WriteFile,
│   │                    # AlignedBuffer, VolumeLockResult, lock/dismount,
│   │                    # is_system_drive, exception hierarchy
│   ├── models.py        # Dataclasses: DriveInfo, SmartAttribute, NvmeHealthInfo,
│   │                    # HealthStatus, BenchmarkResult, SurfaceScanResult,
│   │                    # ScanMode, BlockCategory, InterfaceType (incl. VIRTUAL)
│   ├── drive_enumerator.py  # PhysicalDrive0..31 scan + interface heuristics
│   ├── smart_ata.py     # ATA SMART: legacy IOCTL + ATA PT + SCSI SAT,
│   │                    # check_scsi_status, IDENTIFY DEVICE via SAT
│   ├── smart_nvme.py    # NVMe Health: QueryProperty (3 sizes) → ProtocolCommand
│   │                    # → SCSI_MINIPORT → WMI fallback
│   ├── smart_usb_nvme.py # USB-NVMe: JMicron/ASMedia/Realtek vendor SCSI
│   ├── health_assessor.py  # Health Score (0-100), TBW, WAF, penalties
│   ├── benchmark.py     # 7 benchmark tests + temperature monitoring
│   ├── surface_scan.py  # Surface scan engine (Ignore/Erase/Refresh/Write)
│   └── history.py       # Test history: SQLite (disk_history.db)
├── data/
│   ├── smart_db.py      # 78 SMART attributes (EN/RU), SmartAttributeInfo
│   ├── nvme_fields.py   # 17 NVMe health fields (EN/RU), NvmeFieldInfo
│   ├── vendor_profiles.py   # Vendor decoder profiles (SandForce etc.),
│   │                        # _DEFAULT_DECODE for temperature
│   └── baselines.py     # Performance baselines per drive class (QD1 + QD32)
├── gui/
│   ├── main_window.py   # Main window, menus, export, SMART worker
│   ├── drive_selector.py    # Drive ComboBox
│   ├── info_panel.py    # Drive info display (model, serial, capacity...)
│   ├── smart_table.py   # QTableWidget for ATA/NVMe SMART
│   ├── health_indicator.py  # Health badge (GOOD/WARNING/CRITICAL)
│   ├── benchmark_panel.py   # Result cards + 4 charts + profile selector + worker
│   ├── surface_panel.py     # Block map + stats + worker
│   └── theme.py         # Catppuccin Mocha QSS
├── utils/
│   ├── admin.py         # IsUserAnAdmin check + UAC elevation (ShellExecuteW "runas")
│   └── formatting.py    # Capacity, hours, temperature formatting
└── resources/app.ico    # Application icon
run.py                   # GUI entry point with UAC elevation prompt
cli.py                   # CLI entry point (also pyproject console script)
pyproject.toml           # requires-python >=3.12, PySide6>=6.6.0
```

`pyproject.toml` declares the CLI as a console script:

```toml
[project.scripts]
disk_diag = "cli:main"
```

### Design Principle: GUI → Core → Windows API

```
GUI (PySide6)
  │
  ├── QThread Workers (background tasks)
  │     │
  │     ▼
  Core (pure Python + ctypes)
  │     │
  │     ▼
  Windows API (kernel32.dll)
        │
        ▼
  PhysicalDriveN / \\.\X: / \\.\ScsiN: / \\?\Volume{GUID}
```

Single external dependency: **PySide6**. Disk access is pure `ctypes` + `kernel32.dll` — no WMI libraries, no pywin32 (PowerShell is only spawned as a last-resort NVMe fallback).

### Golden Rules (learned the hard way)

1. **`_pack_ = 1` ONLY for ATA/SMART and NVMe log structures** (`IDEREGS`,
   `SENDCMDINPARAMS`, `SENDCMDOUTPARAMS`, `GETVERSIONINPARAMS`,
   `NVME_HEALTH_INFO_LOG`) — these are fixed on-wire binary formats.
   **Storage API structures** (`STORAGE_PROPERTY_QUERY`, `ATA_PASS_THROUGH_EX`,
   `SCSI_PASS_THROUGH`, `DISK_GEOMETRY_EX`, ...) use **native Windows
   alignment** — no `_pack_`.
2. **Never hardcode Windows structure sizes/offsets.** Always
   `ctypes.sizeof(Struct)` and `Struct.field.offset`. Hardcoded NVMe buffer
   sizes (44/52/564) were the root cause of months of NVMe failures.
3. **INVALID_HANDLE_VALUE** is `ctypes.c_void_p(-1).value` on 64-bit
   (0xFFFFFFFFFFFFFFFF), not `-1`. Also call `SetLastError(0)` before
   `CreateFileW` so a stale error code can't leak into the check.
4. **SCSI/ATA pass-through requires write access** (`read_only=False`), even
   for read-only commands like IDENTIFY. Enumeration opens drives
   `read_only=True`; anything pass-through opens a second RW handle.
5. **Honest benchmarks need `FILE_FLAG_NO_BUFFERING` + `FILE_FLAG_WRITE_THROUGH`**
   plus page-aligned buffers (`VirtualAlloc`) and fresh `os.urandom()` data on
   every write iteration (compressing/deduplicating controllers inflate the
   numbers otherwise).
6. **Destructive operations are fail-closed.** If even one volume on the
   target disk fails to lock/dismount, the write test aborts.

---

## 2. Windows API Layer (winapi.py)

### Exception hierarchy

```
DiskAccessError                # base
├── AdminPrivilegeRequired     # CreateFileW error 5
├── DriveNotFound              # CreateFileW error 2
├── SmartNotSupported
└── IoctlFailed(ioctl_name, error_code, error_msg)
```

### DeviceHandle

```python
class DeviceHandle:
    def __init__(self, drive_number=-1, read_only=False, flags=0, device_path=""):
        # Opens \\.\PhysicalDriveN, or device_path (e.g. \\.\Scsi1:) if given
```

Context manager (`with DeviceHandle(...) as h:`). Methods:
- `ioctl(code, in_struct, out_size)` — DeviceIoControl with a ctypes Structure
- `ioctl_raw(code, in_bytes, out_size)` — DeviceIoControl with raw bytes
- `ioctl_inplace(code, bytearray)` — single buffer for input AND output
  (some NVMe drivers require this; result is copied back into the bytearray)
- `read(ptr, size)` / `write(ptr, size)` — ReadFile / WriteFile at current position
- `read_at(offset, ptr, size)` / `write_at(offset, ptr, size)` — seek + I/O
- `seek(offset)` — SetFilePointerEx (FILE_BEGIN)

`device_path` allows opening arbitrary devices (SCSI adapters `\\.\ScsiN:` for
the NVMe miniport path).

### AlignedBuffer

`VirtualAlloc(MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE)` returns page-aligned
(4096) memory — satisfies the sector-alignment requirement of
`FILE_FLAG_NO_BUFFERING`. Context manager; `free()` calls `VirtualFree`.

### Volume Lock/Dismount (fail-closed)

```python
class VolumeLockResult:
    handles: list          # open volume handles (pass to unlock_volumes)
    failed_volumes: list   # labels where LOCK or DISMOUNT failed

def lock_and_dismount_volumes(drive_number: int) -> VolumeLockResult:
    # 1) Drive letters A-Z → IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS
    #    (keep only volumes residing on drive_number)
    # 2) FindFirstVolumeW/FindNextVolumeW for hidden volumes (EFI, Recovery);
    #    enumeration handle closed in try/finally (no leak on exception)
    # 3) FSCTL_LOCK_VOLUME + FSCTL_DISMOUNT_VOLUME per volume
```

**Contract:** if `failed_volumes` is non-empty, the caller MUST abort
destructive writes (a volume that did not lock is a live filesystem — raw
writes underneath it corrupt data). Both `benchmark.py` and `surface_scan.py`
honor this. Locking is required for ALL write modes (Erase, Refresh, Write) —
without it Windows blocks raw writes into mounted-partition areas (this once
produced 57k phantom "write errors" in Refresh mode).

`unlock_volumes(handles)` closes the handles, releasing the locks.

### System drive detection

```python
def is_system_drive(drive_number: int) -> bool:
    # %SystemDrive% (usually C:) → IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS
    # → compare physical disk number
```

Used as an extra gate before any destructive operation (GUI and CLI).

---

## 3. Drive Enumeration (drive_enumerator.py)

Scans `PhysicalDrive0..31` (`MAX_PHYSICAL_DRIVES = 32`), each opened
`read_only=True`:

- `IOCTL_STORAGE_QUERY_PROPERTY` (StorageDeviceProperty) → model, serial,
  firmware, bus_type. Wrapped in its own try/except — flaky USB bridges must
  not make the whole drive disappear.
- Bus types: ATA=0x03, USB=0x07, SATA=0x0B, NVMe=0x11.
- Capacity, 3 fallbacks: `IOCTL_DISK_GET_LENGTH_INFO` →
  `IOCTL_DISK_GET_DRIVE_GEOMETRY_EX` → `IOCTL_STORAGE_READ_CAPACITY`
  (the last one works even when disk-level IOCTLs fail with error 1117).
- `SMART_GET_VERSION` → `fCapabilities & 0x01` = SMART supported.

### Interface heuristics (in order)

1. **`_detect_hypervisor(model)`** — virtual disk detection by model string:
   VirtIO/"Red Hat", QEMU, "Msft/Microsoft Virtual" (Hyper-V), VMware, VBOX,
   Xen/Citrix, Parallels, Google PersistentDisk, Amazon EBS. Matches set
   `InterfaceType.VIRTUAL` + `DriveInfo.hypervisor`. Virtual disks have no
   physical SMART (it's a hypervisor abstraction over LVM/NFS/Ceph/RAID) —
   the GUI shows an explanatory message instead of attempting reads.
2. **`_looks_nvme_model(model)`** — for `bus_type=Unknown` (OEM drivers like
   Intel RST/VMD): model contains "NVME" or starts with `HFM` (SK hynix OEM),
   `KBG` (KIOXIA), `PM9` (Samsung OEM), `MZV`/`MZ1`/`MZQ` (Samsung M.2/PM983/U.2)
   → forced `NVME`. Explicit exclusions: `MZ7`/`MZN` are Samsung **SATA**.
3. **Generic USB enclosure names** (`_looks_generic_usb_model`: "Mass Storage
   Device", "USB Device", ...) → fetch the real model/serial/firmware via
   `identify_device_via_sat()` over a **second handle opened with
   `read_only=False`** (pass-through needs write access; the read-only
   enumeration handle gets error 5). Getting the real serial matters for
   history.db keys and vendor-profile matching.
4. **Unknown bus + SMART_GET_VERSION responds** → forced `SATA` (Intel
   RST/VMD report Unknown bus for plain SATA SSDs; without this the SMART
   check was skipped entirely).

---

## 4. SMART Reading

### 4.1 ATA/SATA (smart_ata.py)

**Two transports:**

1. **Legacy IOCTL** (`SMART_RCV_DRIVE_DATA`) — internal SATA.
   - `SENDCMDINPARAMS` / `SENDCMDOUTPARAMS` with `_pack_ = 1`
   - `bDriveNumber = 0` always — the device is selected by the handle; the
     field is an IDE master/slave relic
   - `SMART_ENABLE_OPERATIONS` (0xD8) is sent first — some drives (WD,
     Seagate) require it; it is a no-op if SMART is already enabled
2. **Pass-through for USB-SATA bridges** (`read_smart_via_sat`), tried in order:
   - `IOCTL_ATA_PASS_THROUGH` (`ATA_PASS_THROUGH_EX`, taskfile registers)
   - `IOCTL_SCSI_PASS_THROUGH` + SAT CDB: opcode 0x85 (ATA PASS-THROUGH 16),
     protocol PIO Data-In (`4 << 1`), LBA Mid/High = 0x4F/0xC2 SMART signature
   - If ENABLE fails on a method, a direct READ ATTRIBUTES probe decides
     whether the method works (some bridges reject non-data commands)

**`check_scsi_status(result, sense_offset, context)`** — applied to every
`IOCTL_SCSI_PASS_THROUGH` response (SAT SMART, INQUIRY VPD 0x89, SAT IDENTIFY,
and the USB-NVMe bridge commands). `DeviceIoControl` can return success while
the SCSI command itself failed (CHECK CONDITION etc.) — the status byte lives
at offset 2 of the `SCSI_PASS_THROUGH` header. Non-zero status raises
`IoctlFailed` with the SAM-5 status name and hex sense data. Without this
check a garbage data buffer is silently treated as a valid response.

**Parsing:** 512-byte buffer → up to 30 attribute records × 12 bytes starting
at offset 2: `[id][flags:2][current][worst][raw:6][reserved]`. Raw is a
48-bit little-endian int.

**Per-attribute health level:**
```python
if threshold > 0 and current <= threshold:
    CRITICAL
elif threshold > 0 and current < 100 and current <= threshold + 10:
    WARNING   # don't warn when current == 100 (max/best value)!
elif is_critical and (raw & 0xFFFFFFFF) > 0 and id in (5, 196, 197, 198):
    WARNING   # low32 mask — SandForce packs extra data in high bytes
else:
    GOOD
```

**IDENTIFY DEVICE via SAT** (`identify_device_via_sat`), fallback chain:
1. SCSI INQUIRY VPD page 0x89 (ATA Information) — per SAT spec the bridge
   returns a copy of IDENTIFY data at page offset 60
2. `IOCTL_ATA_PASS_THROUGH` with command 0xEC
3. `IOCTL_SCSI_PASS_THROUGH` + SAT CDB 0x85 with command 0xEC

ATA strings swap bytes within each 16-bit word (`_ata_string`); fields are
padded with spaces or NULs — strip both. IDENTIFY offsets: serial = bytes
20–39, firmware = 46–53, model = 54–93.

**Drive type detection** (`detect_drive_type_from_smart`): ≥2 attributes from
`SSD_INDICATOR_ATTRS` → SSD; ≥2 mechanical attributes (3, 7, 10, 189, 191,
220, 240) → HDD; otherwise UNKNOWN.

**Temperature** (ID 194/190): low byte of raw = °C. Kingston and others pack
min/max into the higher bytes.

### 4.2 NVMe (smart_nvme.py)

Fallback chain in `read_nvme_health_auto()`:

```
1. IOCTL_STORAGE_QUERY_PROPERTY on PhysicalDrive:
   RW handle first, then RO × 2 PropertyIds (Device/Adapter protocol-specific)
   × 3 STORAGE_PROTOCOL_SPECIFIC_DATA variants (40B, 44B, 28B)  → 12 combos
2. IOCTL_STORAGE_QUERY_PROPERTY via SCSI adapter \\.\ScsiN:
   (port from IOCTL_SCSI_GET_ADDRESS) × 2 PropertyIds × 3 sizes → 6 combos
3. IOCTL_STORAGE_PROTOCOL_COMMAND — raw NVMe Admin Get Log Page (0x02),
   flags AdapterRequest (0x80000000) then DeviceRequest (0)
4. IOCTL_SCSI_MINIPORT with SRB_IO_CONTROL signature "NvmeMini"
5. PowerShell Get-StorageReliabilityCounter (WMI) — sets wmi_fallback=True
```

The three `STORAGE_PROTOCOL_SPECIFIC_DATA` variants (`_ProtoData10` = 40 B
Win10 1809+/Win11, `_ProtoData11` = 44 B latest SDK, `_ProtoData7` = 28 B
Win10 1607) exist because different Windows versions define different field
counts. The buffer is assembled with `proto_class.from_buffer()`, all offsets
come from `ctypes.sizeof()`, and the response is validated against
`ProtocolDataOffset`/`ProtocolDataLength` from the returned descriptor — never
assumed.

Parsing hardening:
- Composite Temperature sanity check: accept 200–400 K only, else report 0
  ("unknown"); prevents −273 °C from uninitialized fields.
- `media_errors` / `error_log_entries` masked to low 64 bits — SK hynix BC711
  writes garbage into the upper bytes of the 128-bit fields.
- `_wmi_int()` safely converts WMI values (number, "50.5", null) to int.

### 4.3 USB-NVMe Bridges (smart_usb_nvme.py)

USB-NVMe bridges do not pass standard NVMe IOCTLs. Vendor SCSI commands
tunnel NVMe Admin commands instead:

```python
_BRIDGE_METHODS = [
    ("JMicron", _jmicron_get_smart),   # CDB 0xA1, multi-step
    ("ASMedia", _asmedia_get_smart),   # CDB 0xE6, 1-step
    ("Realtek", _realtek_get_smart),   # CDB 0xE4, 1-step
]
```

**JMicron (JMS583/581/586)** hijacks the ATA_PASSTHROUGH_12 opcode 0xA1:
1. DATA_OUT: 512-byte NVMe command page (signature "NVME" + opcode + NSID +
   CDW10) — `cdb[1] = 0x80` (admin, send command)
2. DATA_IN: 512-byte SMART log — `cdb[1] = 0x82` (admin, DMA-in)

**DATA_OUT caveat:** the response contains only the `SCSI_PASS_THROUGH` header,
no data — never length-check a DATA_OUT response. The SCSI status byte IS
checked even for DATA_OUT (a rejected step 1 used to "succeed" silently and
step 2 returned garbage).

**`_looks_valid_health_page(raw)`** — structural validation of the returned
page (a bridge can return 512 bytes of non-zero garbage): Composite
Temperature must be 200–400 K, or (if the firmware leaves it empty) Available
Spare and its Threshold must both be ≤ 100 (they are percentages per spec).

Handles are opened `read_only=False` (pass-through requirement). On success
the caller updates `smart_supported=True`, `drive_type=SSD` on the DriveInfo.

The full USB fallback chain (in the GUI worker and CLI):
**SAT → USB-NVMe bridges → standard NVMe IOCTLs → WMI**.

---

## 5. Health Assessment (health_assessor.py)

### ATA Score formula

```python
score = 100
score -= min(40, reallocated * 2)            # ID 5 (decoded)
score -= min(40, uncorrectable * 5)          # max(ID 187, ID 198) (decoded)
score -= min(30, program_fail * 3)           # ID 171
score -= min(30, erase_fail * 3)             # ID 172
score -= min(20, pending * 4)                # ID 197 (decoded)
# SSD Life Left (ID 231): wear 50/80/90/100% → −5/−15/−25/−30
# Wear Leveling (ID 177, only if 231 absent): → −5/−15/−25
# Temperature (ID 194/190, low byte): >60/70/80 °C → −5/−10/−15
score -= min(10, crc_errors)                 # ID 199
```

Every deduction is recorded as a `(reason, points)` penalty — surfaced in the
GUI breakdown and in exports. "Decoded" means raw passed through the matched
vendor profile (`decode_raw`); without a profile, IDs 5/187/196/197/198 fall
back to a low-32-bit mask (SandForce compatibility).

### NVMe Score

Critical Warning ≠ 0 → −30; media errors → −min(40, n×5); Percentage Used
tiers 50/80/90/100% → −5/−15/−25/−30; Available Spare below threshold → −20
(near threshold → −10, both skipped on WMI fallback); temperature tiers as
ATA; Unsafe Shutdowns >100 → −5, >1000 → −10; Critical Temp Time > 0 → −10.

**WMI fallback guard:** if `wmi_fallback` is set AND power-on hours, data
units written and power cycles are all zero, return `HealthLevel.UNKNOWN`
early — an empty WMI answer must not produce a fake GOOD 100/100.

### TBW / WAF

```python
# ATA: ID 241 (Total Host Writes, LBA sectors) — or ID 233 × 32 MB units
consumed_tb = host_writes_lba * 512 / 1024**4
# NVMe: Data Units Written × 512_000 bytes
consumed_tb = data_units_written * 512000 / 1024**4
# Rated TBW is an ESTIMATE, not a spec:
tbw_estimated_tb = capacity_tb * 600          # TLC consumer heuristic
tbw_estimation_method = "heuristic_600_per_tb"
# Remaining life from POH-derived daily write rate
```

WAF (ATA only): ID 249 (NAND writes, GiB) / host TB, or ID 243 / ID 241.
TBW is not shown for HDDs — `_is_ssd()` requires SSD-specific attributes.

---

## 6. Data Layer (disk_diag/data/)

### smart_db.py — 78 attributes

```python
@dataclass(frozen=True)
class SmartAttributeInfo:
    id: int
    name_en: str; name_ru: str
    desc_en: str; desc_ru: str
    is_critical: bool
    unit: Optional[str] = None

    @property
    def name(self) -> str:
        return tr(self.name_en, self.name_ru)   # evaluated per call —
                                                # language switches live
```

Includes vendor blocks for Silicon Motion/Transcend (150, 151, 159, 160–166,
203, 245, 250) and SSD wear/reserve attributes. `SSD_INDICATOR_ATTRS` is the
set used for SSD detection.

### nvme_fields.py — 17 fields

Same bilingual pattern (`NvmeFieldInfo`), keyed by `NvmeHealthInfo` field name.
Descriptions feed table tooltips and the side description panel.

### vendor_profiles.py — vendor decoder engine

7 profiles (SandForce SF-2281/Kingston SKC300, Kingston A400, Kingston
NV2/NV1, Transcend MTS820/830, Intel, Samsung, SanDisk). Each profile:
`match` (model/firmware substrings) + `decode` rules per attribute ID.

Decode methods: `raw`, `low8`, `low16`, `low20` (SandForce POH), `low32`
(SandForce critical attributes).

```python
profile = match_profile(model, firmware)      # or None
value = decode_raw(profile, attr_id, raw)     # falls back to _DEFAULT_DECODE
```

`_DEFAULT_DECODE` applies **regardless of profile**: IDs 190 and 194
(temperature) are ALWAYS decoded as the low byte.

### baselines.py — performance references

6 classes: `sata_hdd`, `sata_ssd`, `nvme_gen3`, `nvme_gen4`, `usb_30`,
`usb_32_gen2`. **Important:** `rand_4k_*_iops` ranges are **QD1** ranges
(this tool measures synchronously, queue depth 1 — it is a latency test).
Vendor datasheet peaks are QD32+ and stored separately as informational
`qd32_peak_*` fields. `compare_to_baseline()` returns verdicts
(`pass` / `warn` at ≥60% of range-min / `fail`) plus a note explaining the
QD1-vs-QD32 distinction.

---

## 7. Benchmark Engine (benchmark.py)

### Phase Order

```
1. Sequential Read   — 1 warm-up + 3 measured runs → median (1 MB blocks, 512 MB)
2. Random 4K Read    — 5000 reads, QD1 → IOPS + P95/P99/P99.9/P99.99
3. Drive Sweep       — ~200 sample points × 50 MB across the whole disk
── lock_and_dismount_volumes() — FAIL-CLOSED: any failed volume aborts writes ──
4. Sequential Write  — 512 MB, starts at offset 1 GB
5. Random 4K Write   — 1000 writes, offsets ≥ 1 GB
6. Mixed I/O 70/30   — 30 s random R/W, offsets ≥ 1 GB
7. Write-Read-Verify — 256 MB (1 GB in Stress), MD5 per block, starts at 1 GB
8. SLC Cache         — up to 50 GB (100 GB in Stress), cliff detection
── unlock_volumes() in finally ──
```

### MBR/GPT protection

`MBR_PROTECT_BYTES = 1 GiB`. Sequential write, verify and the SLC test start
at this offset; random-write and mixed phases generate offsets in
`[1 GiB, capacity)`. The start offset is **never** shifted back into the
protected zone on small disks — the phase is skipped instead (the old
`min(MBR, capacity-total)` formula could overwrite the MBR on disks < 1.5 GB).

### Profiles

| Profile  | include_write | include_slc | Notes                          |
|----------|---------------|-------------|--------------------------------|
| quick    | no            | no          | read-only, safe                |
| standard | yes           | **no**      | seq/random/mixed/verify writes |
| full     | yes           | yes (50 GB) | all tests                      |
| stress   | yes           | yes (100 GB)| verify 1 GB                    |

`include_slc` is a separate flag from `include_write` so Standard can write
without hammering the NAND with the SLC test.

### Honest results

- `FILE_FLAG_NO_BUFFERING` — bypasses the Windows file cache
- `FILE_FLAG_WRITE_THROUGH` — bypasses the drive's write-back cache
- fresh `os.urandom()` block on **every** write iteration — compressing /
  deduplicating controllers (SandForce) inflate results on repeated patterns
- `AlignedBuffer` (VirtualAlloc) — page-aligned buffers

### Reliability details

- Each write phase runs in `try/except DiskAccessError`: the error is logged,
  appended to `result.io_errors`, and the next phase continues.
- `_poll_temp()` runs in **every** phase at ≥5 s intervals (TEMP_INTERVAL_SEC)
  through a separate SMART handle; the SLC test heats the drive most and
  thermal throttling is easy to confuse with a cache cliff.
- `random_low_sample` flags P99.9/P99.99 as unreliable when n < 10 000.
- `SLC_CLIFF_RATIO = 0.6` — cliff when current speed < initial × 0.6; after
  the cliff another 3 GB is written to capture the stabilized post-cache speed.

---

## 8. Surface Scan Engine (surface_scan.py)

### Constructor validation

`SurfaceScanEngine.__init__` raises `ValueError` for `start_offset < 0`,
`end_offset < 0`, `capacity_bytes <= 0`, `block_size <= 0`,
aligned `start >= end`, or `end > capacity`. The GUI catches `ValueError`
and shows a message box (previously an invalid range silently produced a
1-block scan). Block sizes: 64 KB / 256 KB / 1 MB (default) / 4 MB. The scan
range is specified in LBA in the GUI.

### Main loop

```python
for i in range(total_blocks):
    if mode == WRITE:
        write(zeros)                  # no read — block map shows write speed
    else:
        read(block)                   # sequential, NO seek between blocks
        if ERROR: drill_down()        # sector-level analysis
        elif should_write:
            REFRESH: memmove + write_at(same data)   # counts as "refreshed"
            ERASE:   memset(0) + write_at(zeros)     # bad (and optionally slow) blocks
```

Sequential reading relies on the file pointer advancing automatically — `seek`
happens only after an error or an out-of-band write (`need_seek` flag).
`MAX_CONSECUTIVE_ERRORS = 100` aborts a dead drive scan. `erase_slow=True`
extends Erase to CRITICAL/VERY_SLOW latency categories.

### Drill-down

On a block error the block is re-read in 4096-byte sectors: exact bad LBAs
(reported in 512-byte units) are found, zeros are written **only** to
unreadable sectors, readable sectors keep their data. Each bad LBA goes to
`bad_sector_callback` → real-time GUI list.

### Volume lock (mirrors benchmark.py)

All write modes lock first; `failed_volumes` non-empty → `DiskAccessError`
(fail-closed). The whole scan body runs inside `try/finally` →
`unlock_volumes()` — no leaked volume handles on exceptions.

---

## 9. Test History (history.py)

SQLite database `disk_history.db`, located next to the exe (`sys.frozen`) or
in the project root. Single table `test_history` keyed by drive **serial
number** (the reason real serials matter for USB enclosures): timestamp, tool
version, health score, temperature, TBW, POH, benchmark speeds, WAF, penalties
(JSON), notes.

- `save_test(...)` — called automatically after every SMART read (GUI and CLI)
- `get_history(serial)` — last 50 records
- `get_all_disks()` — aggregated per-serial summary

Every connection is wrapped in `contextlib.closing()` — connections used to
leak in the long-lived GUI process on SQL errors. All functions swallow
exceptions with a warning: history failures must never break diagnostics.

---

## 10. GUI Architecture

### QThread + Signal/Slot pattern

All long operations run in background threads:

```python
worker = _SmartWorker(drive)
thread = QThread()
worker.moveToThread(thread)
thread.started.connect(worker.run)
worker.finished.connect(lambda result, g=gen: self._on_smart_finished(result, g))
```

**Stale-result protection:** `MainWindow` keeps a generation counter
(`_smart_gen`), incremented on every drive switch and captured into the
worker's signal lambdas. A `finished`/`error` signal from an old worker
arriving after the user switched drives is ignored — otherwise one disk's
SMART could render under another disk's name (silent misdiagnosis).

`_SmartWorker` routing: NVMe → `read_nvme_health_auto`; USB → SAT →
USB-NVMe bridges → standard NVMe IOCTLs/WMI; otherwise legacy ATA SMART.
`InterfaceType.VIRTUAL` drives short-circuit with an explanatory message
(where to look for real SMART: smartctl/storcli on the hypervisor) and
"N/A (virtual disk)" in the info panel.

### Tabs and menus

Tabs: **SMART** / **Benchmark** / **Surface Scan**. File menu: Refresh (F5),
Export SMART (Ctrl+S), Export Benchmark (Ctrl+B), Export JSON (Ctrl+J), Exit.
Language menu switches EN/RU live (persisted to `lang.cfg`).

### Custom widgets (QPainter)

- **BlockMapWidget** — 12×12 px cell grid, QTimer 30 fps batched repaints,
  worst-category aggregation when blocks-per-cell > 1
- **LineChartWidget** — generic line chart (Drive Sweep, SLC Cache)
- **LatencyHistogramWidget** — 6 bins with percentages
- **LatencyScatterWidget** — points (offset_gb, latency_us)

Attribute descriptions are stored in `Qt.ItemDataRole.UserRole` of table items
(survives sorting). Critical attributes are highlighted blue
(`QColor(137, 180, 250)` = #89B4FA, Catppuccin Mocha).

---

## 11. Localization (i18n.py)

```python
_lang = "ru"   # default

def tr(en: str, ru: str) -> str:
    return ru if _lang == "ru" else en
```

Both translations live inline at the call site — no keys, no resource files.
`lang.cfg` lives next to the exe (`sys.executable` when `sys.frozen`) or in
the project root during development; it is loaded at import time.

Bilingual data (SMART attributes, NVMe fields, baselines) stores
`name_en`/`name_ru` pairs and resolves through `tr()` in properties, so
switching the language re-renders without restart.

To add a string: wrap it in `tr("English", "Русский")`
(`from ..i18n import tr`).

---

## 12. CLI Mode (cli.py)

```
disk_diag --list                  # list drives
disk_diag --smart 0 [--json]      # SMART + health score (autosaves history)
disk_diag --benchmark 0 [--write] [--profile quick|standard|full|stress]
disk_diag --history SERIAL|all    # test history
```

Entry point: `main()` (also exposed as the `disk_diag` console script via
pyproject). Safety gates for `--benchmark --write`:

1. `quick` + `--write` is contradictory → auto-bumped to `standard`.
2. The full write plan (phases, volumes, "data will be DESTROYED") is printed
   **before** anything runs.
3. `is_system_drive()` → refuses without `--force-system-drive`.
4. Interactive confirmation: the user must type the target disk's **serial
   number** (or `DESTROY` if the serial is unknown). `--yes` skips this for
   scripted use.

---

## 13. Build & Distribution

```bash
python -m pip install pyinstaller
python -m PyInstaller --onefile --windowed \
    --name "DISK_Diagnostic" \
    --icon "disk_diag/resources/app.ico" \
    --clean run.py
```

> Always `python -m pip` / `python -m PyInstaller`, never bare `pip` — with
> multiple Pythons installed (3.12/3.14) they can target different
> interpreters. `pyproject.toml` requires Python ≥ 3.12.

`run.py` checks `IsUserAnAdmin()` before importing the app; if not elevated it
offers a UAC restart via `ShellExecuteW(..., "runas", ...)`. SMART access
requires Administrator.

The icon is generated programmatically (PySide6 QPainter → PNG → ICO).

---

## 14. Known Issues & Solutions

| Issue | Cause | Fix |
|-------|-------|-----|
| SandForce packed raw | Controller packs counters into the 6 raw bytes | Vendor profile: low32 for critical attrs, low20 for POH, low8 for temp |
| USB-NVMe DATA_OUT | SCSI response = header only | Never length-check DATA_OUT responses |
| USB bridge returns garbage with success status | DeviceIoControl OK ≠ SCSI command OK | `check_scsi_status()` on every SCSI PT response + `_looks_valid_health_page()` |
| False write errors in Refresh | Volumes not locked | `lock_and_dismount_volumes` for ALL write modes |
| Destructive write over a live FS | Partial volume lock treated as OK | Fail-closed `VolumeLockResult.failed_volumes` → abort |
| False WARNING at current=100 | threshold+10 ≥ 100 | Require `current < 100` before warning |
| SMART of disk A shown under disk B | Stale worker signal after drive switch | Generation counter in MainWindow |
| NVMe −273 °C | Empty/garbage temperature field | 200–400 K sanity bounds |
| Fake GOOD 100/100 via WMI | Empty reliability counters | Early UNKNOWN when POH=DUW=cycles=0 |
| MBR destroyed on tiny disks | Start offset shifted below 1 GB | Never shift into protected zone; skip phase |
| PySide6 on WinServer 2016 | Qt 6.5+ requires Win10 1809+ | No fix, Qt limitation |

---

## 15. How to Add...

### New SMART attribute

1. Add to `disk_diag/data/smart_db.py`:
```python
NEW_ID: _a(NEW_ID, "English Name", "Русское имя",
           "English description", "Русское описание",
           is_critical, "unit"),
```
2. If SSD-specific → add the ID to `SSD_INDICATOR_ATTRS`.

### New vendor decode profile

Add a dict to `VENDOR_PROFILES` in `disk_diag/data/vendor_profiles.py`:
```python
{
    "name": "Vendor Model (controller)",
    "match": {"model_contains": ["PATTERN1", "PATTERN2"]},
    "decode": {9: {"method": "low20"}, 194: {"method": "low8"}},
    "confidence": "high",
},
```

### New USB-NVMe bridge

1. Add a function in `disk_diag/core/smart_usb_nvme.py`:
```python
def _newbridge_get_smart(handle: DeviceHandle) -> bytes:
    cdb = bytearray(16)
    cdb[0] = 0xXX   # vendor opcode
    # ... fill the CDB
    return _scsi_cmd(handle, cdb, _DATA_IN, _SMART_SIZE)
```
2. Append to `_BRIDGE_METHODS`. `_scsi_cmd` already handles the buffer
   layout, SCSI status checking and DATA_OUT semantics.

### New benchmark test

1. Add `_run_new_test()` in `disk_diag/core/benchmark.py`.
2. Add result fields to `BenchmarkResult` (`core/models.py`).
3. Register the phase in `run()` — read phases before the volume lock, write
   phases in the `write_phases` list (gets fail-closed locking, per-phase
   error handling and `finally` unlock for free). Respect
   `MBR_PROTECT_BYTES` if the phase writes.
4. GUI: add a result card in `benchmark_panel.py` and update
   `_on_progress` / `_on_finished`.

### New localization string

Wrap it: `tr("English", "Русский")`. Import: `from ..i18n import tr`.

---

*DISK Diagnostic Tool v2.3.7 — Developer Guide*
*Developed by Serg and Claudine (Anthropic AI)*
