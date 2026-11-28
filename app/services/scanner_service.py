import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile
from typing import Any, Optional
from fastapi import status
from PIL import Image, ImageOps
from app.core.exceptions import AppError, BadRequestError
from app.core.logging_config import logger
from app.services.photo_service import photo_service

_WIA_LIST_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$dm = New-Object -ComObject WIA.DeviceManager
$devices = @()
foreach ($d in $dm.DeviceInfos) {
    if ([int]$d.Type -eq 1) {
        $name = ''
        $mfg = ''
        $desc = ''
        try { $name = [string]$d.Properties.Item('Name').Value } catch {}
        try { $mfg = [string]$d.Properties.Item('Manufacturer').Value } catch {}
        try { $desc = [string]$d.Properties.Item('Description').Value } catch {}
        $devices += [PSCustomObject]@{
            id = [string]$d.DeviceID
            name = if ($name) { $name } else { [string]$d.DeviceID }
            manufacturer = $mfg
            description = $desc
            backend = 'wia'
        }
    }
}
ConvertTo-Json -InputObject @($devices) -Compress
"""

_WIA_SCAN_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$targetId = $env:WIMS_WIA_DEVICE_ID
$dpi = [int]$env:WIMS_WIA_DPI
$colorMode = $env:WIMS_WIA_COLOR
$sourceMode = $env:WIMS_WIA_SOURCE
$outPath = $env:WIMS_WIA_OUT

$dm = New-Object -ComObject WIA.DeviceManager
$selectedInfo = $null
foreach ($d in $dm.DeviceInfos) {
    if ([int]$d.Type -eq 1) {
        if (-not $targetId -or [string]$d.DeviceID -eq $targetId) {
            $selectedInfo = $d
            break
        }
    }
}
if ($null -eq $selectedInfo) {
    throw "Requested scanner device was not found."
}

$scannerName = [string]$selectedInfo.DeviceID
try {
    $n = [string]$selectedInfo.Properties.Item('Name').Value
    if ($n) { $scannerName = $n }
} catch {}

$dev = $selectedInfo.Connect()
if ($sourceMode -eq 'feeder') {
    try { $dev.Properties.Item('3088').Value = 1 } catch {}
} else {
    try { $dev.Properties.Item('3088').Value = 2 } catch {}
}

$item = $dev.Items.Item(1)
$intent = switch ($colorMode) {
    'grayscale' { 2 }
    'bw' { 4 }
    default { 1 }
}
try { $item.Properties.Item('6146').Value = $intent } catch {}
try { $item.Properties.Item('6147').Value = $dpi } catch {}
try { $item.Properties.Item('6148').Value = $dpi } catch {}

$imgFile = $null
try {
    # FormatID BMP {B96B3CAB-0728-11D3-9D7B-0000F81EF32E}
    $imgFile = $item.Transfer("{B96B3CAB-0728-11D3-9D7B-0000F81EF32E}")
} catch {
    $imgFile = $item.Transfer()
}
if ($null -eq $imgFile) {
    throw "Scanner returned no image data."
}
if (Test-Path $outPath) {
    Remove-Item -Force $outPath
}
$imgFile.SaveFile($outPath)
ConvertTo-Json -InputObject @{ scanner_name = $scannerName; out_path = $outPath } -Compress
"""


class ScannerService:
    """Hardware scanner abstraction supporting Windows WIA and Linux SANE."""

    def _run_powershell_json(
        self,
        script: str,
        *,
        env_overrides: Optional[dict[str, str]] = None,
        timeout_seconds: float = 20.0,
    ) -> Any:
        exe = shutil.which("powershell") or shutil.which("pwsh")
        if not exe:
            raise AppError(
                "PowerShell is not available to access Windows WIA scanners.",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                error_code="scanner_unavailable",
            )
        env = os.environ.copy()
        if env_overrides:
            env.update(env_overrides)
        proc = subprocess.run(  # noqa: S603
            [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-STA", "-Command", "-"],
            input=script,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            env=env,
        )
        if proc.returncode != 0:
            err_detail = (proc.stderr or proc.stdout or "Unknown scanner error").strip()
            raise AppError(
                f"Scanner operation failed: {err_detail[:280]}",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                error_code="scanner_error",
            )
        raw_out = (proc.stdout or "").strip()
        if not raw_out:
            return []
        try:
            return json.loads(raw_out)
        except json.JSONDecodeError as exc:
            raise AppError(
                f"Unexpected scanner response format: {exc}",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                error_code="scanner_error",
            ) from exc

    def _list_sane_scanners(self) -> dict[str, Any]:
        scanimage = shutil.which("scanimage")
        if not scanimage:
            return {
                "available": False,
                "platform": platform.system(),
                "backend": "sane",
                "devices": [],
                "message": "SANE 'scanimage' is not installed on this host.",
            }
        try:
            proc = subprocess.run([scanimage, "-L"], text=True, capture_output=True, timeout=12.0)  # noqa: S603
            devices = []
            for line in (proc.stdout or "").splitlines():
                m = re.match(r"^device\s+`([^']+)'\s+is\s+a\s+(.+)$", line.strip())
                if m:
                    devices.append(
                        {
                            "id": m.group(1),
                            "name": m.group(2).strip(),
                            "manufacturer": None,
                            "description": None,
                            "backend": "sane",
                        }
                    )
            return {
                "available": True,
                "platform": platform.system(),
                "backend": "sane",
                "devices": devices,
                "message": None if devices else "No SANE scanners detected.",
            }
        except Exception as exc:
            return {
                "available": False,
                "platform": platform.system(),
                "backend": "sane",
                "devices": [],
                "message": str(exc),
            }

    def list_scanners(self) -> dict[str, Any]:
        sys_name = platform.system()
        if sys_name == "Windows":
            try:
                raw = self._run_powershell_json(_WIA_LIST_SCRIPT, timeout_seconds=15.0)
                if isinstance(raw, dict):
                    raw = [raw]
                devices = [
                    {
                        "id": str(d.get("id") or ""),
                        "name": str(d.get("name") or d.get("id") or "WIA Scanner"),
                        "manufacturer": (str(d.get("manufacturer") or "").strip() or None),
                        "description": (str(d.get("description") or "").strip() or None),
                        "backend": "wia",
                    }
                    for d in (raw or [])
                    if isinstance(d, dict) and d.get("id")
                ]
                return {
                    "available": True,
                    "platform": sys_name,
                    "backend": "wia",
                    "devices": devices,
                    "message": None if devices else "No WIA scanners found. Make sure your scanner is powered on and connected.",
                }
            except AppError as exc:
                logger.warning("WIA scanner enumeration failed: %s", exc.detail)
                return {
                    "available": False,
                    "platform": sys_name,
                    "backend": "wia",
                    "devices": [],
                    "message": exc.detail,
                }
            except subprocess.TimeoutExpired:
                return {
                    "available": False,
                    "platform": sys_name,
                    "backend": "wia",
                    "devices": [],
                    "message": "Timed out waiting for WIA scanner discovery.",
                }
        return self._list_sane_scanners()

    def scan_page(
        self,
        *,
        device_id: Optional[str] = None,
        dpi: int = 300,
        color_mode: str = "color",
        source: str = "flatbed",
    ) -> dict[str, Any]:
        listing = self.list_scanners()
        devices = listing.get("devices") or []
        if not devices:
            raise BadRequestError(listing.get("message") or "No hardware scanner is available.")

        selected = devices[0]
        if device_id:
            match = next((d for d in devices if d["id"] == device_id), None)
            if not match:
                raise BadRequestError("Selected scanner was not found. Refresh the scanner list and try again.")
            selected = match

        backend = selected.get("backend", listing.get("backend", "wia"))
        with tempfile.TemporaryDirectory(prefix="wims_scan_") as tmp_dir:
            if backend == "wia":
                out_file = Path(tmp_dir) / "scanned_page.bmp"
                try:
                    result = self._run_powershell_json(
                        _WIA_SCAN_SCRIPT,
                        env_overrides={
                            "WIMS_WIA_DEVICE_ID": str(selected["id"]),
                            "WIMS_WIA_DPI": str(int(dpi)),
                            "WIMS_WIA_COLOR": str(color_mode),
                            "WIMS_WIA_SOURCE": str(source),
                            "WIMS_WIA_OUT": str(out_file),
                        },
                        timeout_seconds=75.0,
                    )
                except subprocess.TimeoutExpired as exc:
                    raise AppError(
                        "Scanner timed out while scanning the page.",
                        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                        error_code="scanner_timeout",
                    ) from exc

                actual_path = Path(result.get("out_path") or out_file) if isinstance(result, dict) else out_file
                if not actual_path.exists():
                    raise BadRequestError("Scanner finished without producing an image file.")
                scanner_name = (
                    str(result.get("scanner_name") or selected["name"])
                    if isinstance(result, dict)
                    else selected["name"]
                )
            else:
                scanimage = shutil.which("scanimage")
                if not scanimage:
                    raise BadRequestError("SANE scanimage backend is not available.")
                actual_path = Path(tmp_dir) / "scanned_page.png"
                sane_mode = {"grayscale": "Gray", "bw": "Lineart"}.get(color_mode, "Color")
                cmd = [
                    scanimage,
                    "-d",
                    str(selected["id"]),
                    "--resolution",
                    str(int(dpi)),
                    "--mode",
                    sane_mode,
                    "--format=png",
                    "-o",
                    str(actual_path),
                ]
                proc = subprocess.run(cmd, text=True, capture_output=True, timeout=75.0)  # noqa: S603
                if proc.returncode != 0 or not actual_path.exists():
                    raise BadRequestError((proc.stderr or "SANE scan failed.").strip()[:240])
                scanner_name = selected["name"]

            with Image.open(actual_path) as raw_img:
                oriented = ImageOps.exif_transpose(raw_img)
                saved = photo_service.save_pil_image(
                    oriented,
                    ext=".png",
                    max_dimension=3600,
                    prefix="scan_",
                )

        return {
            "original_path": saved["original_path"],
            "thumbnail_path": saved["thumbnail_path"],
            "filename": saved["filename"],
            "width": saved["width"],
            "height": saved["height"],
            "scanner_name": scanner_name,
        }


scanner_service = ScannerService()
