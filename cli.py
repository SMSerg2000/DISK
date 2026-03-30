"""DISK Diagnostic Tool — CLI mode (без GUI).

Использование:
    diskdiag.exe --smart 0              # SMART диска 0
    diskdiag.exe --smart 0 --json       # SMART в JSON
    diskdiag.exe --benchmark 0          # Бенчмарк диска 0
    diskdiag.exe --benchmark 0 --write  # Бенчмарк с записью
    diskdiag.exe --list                 # Список дисков
    diskdiag.exe --history S/N          # История тестов по серийному номеру
"""

import argparse
import json
import sys
import logging

# Настроим logging до импорта модулей
logging.basicConfig(level=logging.WARNING, format="%(message)s")
logger = logging.getLogger("cli")


def cmd_list():
    """Список дисков."""
    from disk_diag.core.drive_enumerator import enumerate_drives
    drives = enumerate_drives()
    if not drives:
        print("No drives found. Run as Administrator.")
        return 1
    for d in drives:
        print(f"  Disk {d.drive_number}: {d.model.strip()} "
              f"({d.capacity_bytes / (1024**3):.1f} GB, {d.interface_type.value})")
    return 0


def cmd_smart(drive_number: int, as_json: bool):
    """Прочитать SMART."""
    from disk_diag.core.drive_enumerator import enumerate_drives
    from disk_diag.core.smart_ata import read_smart_attributes, read_smart_via_sat
    from disk_diag.core.smart_nvme import read_nvme_health_auto
    from disk_diag.core.smart_usb_nvme import read_usb_nvme_smart
    from disk_diag.core.health_assessor import assess_ata_health, assess_nvme_health
    from disk_diag.core.winapi import DeviceHandle
    from disk_diag.core.models import InterfaceType
    from disk_diag.core.history import save_test
    from disk_diag import __version__

    drives = enumerate_drives()
    drive = None
    for d in drives:
        if d.drive_number == drive_number:
            drive = d
            break
    if not drive:
        print(f"Disk {drive_number} not found.")
        return 1

    print(f"Disk {drive_number}: {drive.model.strip()} ({drive.capacity_bytes / (1024**3):.1f} GB)")

    # Читаем SMART
    data_type = "none"
    attrs = []
    health_info = None
    status = None

    if drive.interface_type == InterfaceType.NVME:
        health_info = read_nvme_health_auto(drive_number)
        status = assess_nvme_health(health_info, drive.capacity_bytes)
        data_type = "nvme"
    elif drive.interface_type == InterfaceType.USB:
        with DeviceHandle(drive_number) as h:
            attrs = read_smart_via_sat(h)
        if attrs:
            status = assess_ata_health(attrs, drive.capacity_bytes)
            data_type = "ata"
        else:
            health_info = read_usb_nvme_smart(drive_number)
            if health_info:
                status = assess_nvme_health(health_info, drive.capacity_bytes)
                data_type = "nvme"
            else:
                try:
                    health_info = read_nvme_health_auto(drive_number)
                    status = assess_nvme_health(health_info, drive.capacity_bytes)
                    data_type = "nvme"
                except Exception:
                    pass
    elif drive.smart_supported:
        with DeviceHandle(drive_number) as h:
            attrs = read_smart_attributes(h, drive_number)
            status = assess_ata_health(attrs, drive.capacity_bytes)
            data_type = "ata"

    if not status:
        print("SMART not available for this drive.")
        return 1

    # Сохранить в историю
    save_test(
        serial=drive.serial_number or "",
        model=drive.model.strip(),
        version=__version__,
        health_score=status.health_score,
        tbw_consumed_tb=status.tbw_consumed_tb,
        power_on_hours=status.power_on_hours,
        waf=status.waf,
        penalties=[(r, p) for r, p in status.penalties],
    )

    if as_json:
        result = {
            "device": {
                "model": drive.model.strip(),
                "serial": drive.serial_number,
                "capacity_gb": round(drive.capacity_bytes / (1024**3), 1),
                "interface": drive.interface_type.value,
            },
            "health": {
                "score": status.health_score,
                "level": status.level.value,
                "penalties": [{"reason": r, "points": p} for r, p in status.penalties],
                "tbw_consumed_tb": status.tbw_consumed_tb,
                "power_on_hours": status.power_on_hours,
                "waf": status.waf,
            }
        }
        if data_type == "ata":
            result["smart"] = [
                {"id": a.id, "name": a.name, "current": a.current,
                 "worst": a.worst, "threshold": a.threshold,
                 "raw_value": a.raw_value, "health": a.health_level.value}
                for a in attrs
            ]
        elif data_type == "nvme" and health_info:
            result["smart"] = {
                "temperature": health_info.temperature_celsius,
                "available_spare": health_info.available_spare,
                "percentage_used": health_info.percentage_used,
                "power_on_hours": health_info.power_on_hours,
                "media_errors": health_info.media_errors,
            }
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        # Текстовый вывод
        print(f"\nHealth: {status.level.value.upper()} (Score: {status.health_score}/100)")
        if status.penalties:
            for reason, pts in status.penalties:
                print(f"  -{pts}: {reason}")
        if status.power_on_hours > 0:
            print(f"Power-On Hours: {status.power_on_hours:,}")
        if status.tbw_consumed_tb > 0:
            print(f"TBW: {status.tbw_consumed_tb:.1f} TB")
        if status.waf > 0:
            print(f"WAF: {status.waf:.1f}")
        print()

        if data_type == "ata":
            print(f"{'ID':<5} {'Attribute':<30} {'Cur':>5} {'Wst':>5} {'Thr':>5} {'Raw':>16}")
            print("-" * 70)
            for a in attrs:
                print(f"{a.id:<5} {a.name:<30} {a.current:>5} {a.worst:>5} "
                      f"{a.threshold:>5} {a.raw_value:>16,}")
        elif data_type == "nvme" and health_info:
            h = health_info
            print(f"Temperature:     {h.temperature_celsius}°C")
            print(f"Available Spare: {h.available_spare}%")
            print(f"Percentage Used: {h.percentage_used}%")
            print(f"Power-On Hours:  {h.power_on_hours:,}")
            print(f"Media Errors:    {h.media_errors}")

    return 0


def cmd_benchmark(drive_number: int, include_write: bool):
    """Запустить бенчмарк."""
    from disk_diag.core.drive_enumerator import enumerate_drives
    from disk_diag.core.benchmark import BenchmarkEngine

    drives = enumerate_drives()
    drive = None
    for d in drives:
        if d.drive_number == drive_number:
            drive = d
            break
    if not drive:
        print(f"Disk {drive_number} not found.")
        return 1

    print(f"Benchmarking: {drive.model.strip()} ({drive.capacity_bytes / (1024**3):.1f} GB)")

    def progress(phase, pct, msg):
        print(f"\r  [{phase}] {pct*100:.0f}% {msg}          ", end="", flush=True)

    engine = BenchmarkEngine(drive_number, drive.capacity_bytes,
                             include_write, drive.interface_type.value)
    result = engine.run(progress=progress)
    print()

    print(f"\n{'Test':<25} {'Result':>15}")
    print("-" * 42)
    if result.sequential_speed_mbps > 0:
        print(f"{'Seq Read':<25} {result.sequential_speed_mbps:>12.1f} MB/s")
    if result.seq_write_speed_mbps > 0:
        print(f"{'Seq Write':<25} {result.seq_write_speed_mbps:>12.1f} MB/s")
    if result.random_reads_count > 0:
        print(f"{'Random 4K Read':<25} {result.random_iops:>12,.0f} IOPS")
    if result.random_write_count > 0:
        print(f"{'Random 4K Write':<25} {result.random_write_iops:>12,.0f} IOPS")
    if result.mixed_count > 0:
        print(f"{'Mixed I/O 70/30':<25} {result.mixed_total_iops:>12,.0f} IOPS")
    if result.verify_blocks_tested > 0:
        v = "PASS" if result.verify_blocks_failed == 0 else f"FAIL ({result.verify_blocks_failed})"
        print(f"{'Write-Read-Verify':<25} {v:>15}")
    if result.slc_cache_size_gb > 0:
        print(f"{'SLC Cache':<25} {result.slc_cache_size_gb:>12.1f} GB")
    elif result.slc_speed_mbps > 0:
        print(f"{'SLC Cache':<25} {'No cliff':>15}")

    if result.io_errors:
        print(f"\nI/O Errors: {len(result.io_errors)}")
        for err in result.io_errors:
            print(f"  {err}")

    return 0


def cmd_history(serial: str):
    """История тестов."""
    from disk_diag.core.history import get_history, get_all_disks

    if serial == "all":
        disks = get_all_disks()
        if not disks:
            print("No test history found.")
            return 0
        print(f"{'Serial':<20} {'Model':<30} {'Tests':>5} {'Last Test':<20}")
        print("-" * 78)
        for d in disks:
            print(f"{d['serial'][:20]:<20} {d['model'][:30]:<30} "
                  f"{d['test_count']:>5} {d['last_test'][:19]}")
    else:
        history = get_history(serial)
        if not history:
            print(f"No history for serial: {serial}")
            return 0
        print(f"History for {serial}:")
        print(f"{'Date':<20} {'Score':>5} {'TBW':>8} {'POH':>8}")
        print("-" * 45)
        for h in history:
            tbw = f"{h['tbw_consumed_tb']:.1f}" if h['tbw_consumed_tb'] and h['tbw_consumed_tb'] > 0 else "—"
            poh = f"{h['power_on_hours']:,}" if h['power_on_hours'] and h['power_on_hours'] > 0 else "—"
            print(f"{h['timestamp'][:19]:<20} {h['health_score']:>5} {tbw:>8} {poh:>8}")

    return 0


def main():
    parser = argparse.ArgumentParser(
        prog="DISK_Diagnostic",
        description="DISK Diagnostic Tool — CLI mode"
    )
    parser.add_argument("--list", action="store_true", help="List all drives")
    parser.add_argument("--smart", type=int, metavar="N", help="Read SMART from disk N")
    parser.add_argument("--benchmark", type=int, metavar="N", help="Benchmark disk N")
    parser.add_argument("--write", action="store_true", help="Include write tests (destructive!)")
    parser.add_argument("--json", action="store_true", help="Output in JSON format")
    parser.add_argument("--history", type=str, metavar="SERIAL", help="Show test history (or 'all')")

    args = parser.parse_args()

    if args.list:
        sys.exit(cmd_list())
    elif args.smart is not None:
        sys.exit(cmd_smart(args.smart, args.json))
    elif args.benchmark is not None:
        sys.exit(cmd_benchmark(args.benchmark, args.write))
    elif args.history:
        sys.exit(cmd_history(args.history))
    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
