"""Internal diagnostic agent and Diagnostic Bridge client."""

from __future__ import annotations

import json
import os
import socket
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from jnius import autoclass

SCHEMA = "yj64.diagnostic.v1"
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "bridge.json"


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def write_status(service: Any, payload: dict[str, Any]) -> None:
    path = Path(str(service.getFilesDir())) / "diagnostic-agent-status.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def spool_path(service: Any, filename: str) -> Path:
    return Path(str(service.getFilesDir())) / filename


def make_report(agent_id: str, event: str, **data: Any) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "agent": agent_id,
        "event": event,
        "timestamp_ms": int(time.time() * 1000),
        "report_id": uuid.uuid4().hex,
        "data": data,
    }


def append_spool(service: Any, path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(report, sort_keys=True) + "\n")


def send_bridge(
    host: str,
    port: int,
    token: str,
    timeout: float,
    report: dict[str, Any],
) -> bool:
    envelope = {"token": token, "report": report}
    payload = (json.dumps(envelope, sort_keys=True) + "\n").encode("utf-8")
    try:
        with socket.create_connection((host, port), timeout=timeout) as connection:
            connection.sendall(payload)
            connection.settimeout(timeout)
            response = connection.recv(4096).decode("utf-8", errors="replace").strip()
            if not response:
                return False
            ack = json.loads(response)
            return (
                ack.get("ok") is True
                and ack.get("report_id") == report["report_id"]
            )
    except (OSError, ValueError, TypeError):
        return False


def send_with_retry(config: dict[str, Any], report: dict[str, Any]) -> bool:
    bridge = config["bridge"]
    attempts = int(bridge["retry_count"])
    delay = float(bridge["retry_delay_seconds"])
    for attempt in range(max(1, attempts)):
        if send_bridge(
            str(bridge["host"]),
            int(bridge["port"]),
            str(bridge["token"]),
            float(bridge["connect_timeout_seconds"]),
            report,
        ):
            return True
        if attempt + 1 < attempts:
            time.sleep(delay)
    return False


def upload_https(endpoint: str, report: dict[str, Any], timeout: float = 5.0) -> bool:
    if not endpoint:
        return False
    try:
        body = json.dumps(report).encode("utf-8")
        req = request.Request(
            endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with request.urlopen(req, timeout=timeout) as response:
            return 200 <= int(response.status) < 300
    except (OSError, ValueError, TypeError):
        return False


def self_diagnostic(config: dict[str, Any], service: Any) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    try:
        config["agent_id"]
        config["target_package"]
        config["bridge"]["host"]
        config["bridge"]["port"]
        checks["configuration"] = "ok"
    except (KeyError, TypeError):
        checks["configuration"] = "invalid"

    try:
        probe = Path(str(service.getFilesDir())) / ".diagnostic_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        checks["storage"] = "ok"
    except OSError:
        checks["storage"] = "unavailable"

    try:
        package_manager = service.getPackageManager()
        visible = package_manager.getLaunchIntentForPackage(
            str(config["target_package"])
        ) is not None
        checks["target_visibility"] = "visible" if visible else "not_visible"
    except Exception as exc:
        checks["target_visibility"] = f"probe_error:{type(exc).__name__}"

    checks["process"] = "ok"
    return checks


def launch_target(package_name: str) -> tuple[bool, str]:
    try:
        context = autoclass("org.kivy.android.PythonService").mService
        package_manager = context.getPackageManager()
        intent = package_manager.getLaunchIntentForPackage(package_name)
        if intent is None:
            return False, "target_package_not_installed"
        Intent = autoclass("android.content.Intent")
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        context.startActivity(intent)
        return True, "launched"
    except Exception as exc:
        return False, f"launch_error:{type(exc).__name__}"


def run() -> None:
    PythonService = autoclass("org.kivy.android.PythonService")
    service = PythonService.mService
    service.setAutoRestartService(True)
    config = load_config()
    agent_id = str(config["agent_id"])
    spool = spool_path(service, str(config["report"]["spool_filename"]))

    startup = make_report(
        agent_id,
        "internal_startup",
        pid=os.getpid(),
        python_version=sys.version.split()[0],
        target_package=config["target_package"],
        cwd=os.getcwd(),
    )
    bridge_ok = send_with_retry(config, startup)
    if not bridge_ok:
        append_spool(service, spool, startup)

    launch_ok, launch_reason = launch_target(str(config["target_package"]))
    launch_report = make_report(
        agent_id,
        "target_launch_result",
        target_package=config["target_package"],
        success=launch_ok,
        reason=launch_reason,
        bridge_received_startup=bridge_ok,
    )
    launch_bridge_ok = send_with_retry(config, launch_report)
    if not launch_bridge_ok:
        append_spool(service, spool, launch_report)

    write_status(
        service,
        {
            "agent": agent_id,
            "event": "target_launch_result",
            "bridge_status": "connected" if diagnostic_bridge_ok and bridge_ok else "spooled",
            "target_launch": launch_reason,
            "target_success": launch_ok,
            "launch_report_sent": launch_bridge_ok,
        },
    )

    while True:
        time.sleep(30)


run()
