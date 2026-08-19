"""Persistent socket-service coverage without loading Qiskit or Aer."""

from __future__ import annotations

import json
import signal
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

from eic_quantum.services import direct_centauro_quantum_worker


class FakeWorker:
    """Only the administrative-ready path is used by this service test."""

    def lifecycle_settings(self) -> dict[str, int]:
        return {"shots": 512, "seed": 314159}


def _ready_request(socket_path: Path, nonce: str) -> dict[str, object]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(2)
        connection.connect(str(socket_path))
        connection.sendall(json.dumps({"schema_version": "active-holdout-admin/v1", "operation": "ready", "nonce": nonce}).encode("utf-8") + b"\n")
        return json.loads(connection.makefile("rb").readline().decode("utf-8"))


def test_disconnected_response_client_does_not_stop_persistent_service(monkeypatch) -> None:
    with tempfile.TemporaryDirectory(dir="/tmp", prefix="qcw-") as directory:
        run_dir = Path(directory)
        socket_path = run_dir / "worker.sock"
        lifecycle_path = run_dir / "worker.lifecycle.json"
        stop_event = threading.Event()
        thread = threading.Thread(
            target=direct_centauro_quantum_worker.serve,
            args=(socket_path, FakeWorker(), lifecycle_path, stop_event),
            daemon=True,
        )
        thread.start()
        for _ in range(100):
            if socket_path.is_socket():
                break
            time.sleep(0.01)
        assert socket_path.is_socket()

        original_send = direct_centauro_quantum_worker._send_response
        send_attempts = 0

        def fail_first_response(connection: socket.socket, response: str) -> None:
            nonlocal send_attempts
            send_attempts += 1
            if send_attempts == 1:
                raise BrokenPipeError("test client disconnected")
            original_send(connection, response)

        monkeypatch.setattr(direct_centauro_quantum_worker, "_send_response", fail_first_response)

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as disconnected:
            disconnected.connect(str(socket_path))
            disconnected.sendall(json.dumps({"schema_version": "active-holdout-admin/v1", "operation": "ready", "nonce": "first-client-nonce"}).encode("utf-8") + b"\n")

        response = _ready_request(socket_path, "second-client-nonce")
        assert response["result"] == "ok"
        assert response["nonce"] == "second-client-nonce"

        stop_event.set()
        thread.join(timeout=2)
        assert not thread.is_alive()
        lifecycle = json.loads(lifecycle_path.read_text(encoding="utf-8"))
        assert lifecycle["state"] == "stopped"
        assert lifecycle["client_disconnects"] >= 1
        assert lifecycle["requests_served"] == 0  # Administrative ready requests do not invoke selection.
        assert send_attempts >= 2


def test_worker_readiness_and_activity_messages_use_bounded_metadata(monkeypatch, capsys) -> None:
    with tempfile.TemporaryDirectory(dir="/tmp", prefix="qcw-") as directory:
        run_dir = Path(directory)
        socket_path = run_dir / "worker.sock"
        stop_event = threading.Event()
        monkeypatch.setattr(direct_centauro_quantum_worker, "validate_request", lambda _request: {"event": 7, "iteration": 3})
        monkeypatch.setattr(direct_centauro_quantum_worker, "process_blind_request", lambda *_args, **_kwargs: {"status": "ok"})
        monkeypatch.setattr(direct_centauro_quantum_worker, "attach_worker_metadata", lambda response, **_kwargs: response)
        thread = threading.Thread(
            target=direct_centauro_quantum_worker.serve,
            args=(socket_path, FakeWorker(), None, stop_event),
            daemon=True,
        )
        thread.start()
        for _ in range(100):
            if socket_path.is_socket():
                break
            time.sleep(0.01)
        assert socket_path.is_socket()

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(2)
            connection.connect(str(socket_path))
            connection.sendall(b"{}\n")
            assert json.loads(connection.makefile("rb").readline().decode("utf-8")) == {"status": "ok"}

        stop_event.set()
        thread.join(timeout=2)
        assert not thread.is_alive()
        output = capsys.readouterr().out
        assert f"Worker ready at {socket_path}. Keep this terminal open" in output
        assert "Processed selector request #1 (event 7, iteration 3)" in output


def test_main_uses_stop_event_for_sigint_and_reports_clean_shutdown(tmp_path: Path, monkeypatch, capsys) -> None:
    class MainWorker:
        def __init__(self, *_args: object) -> None:
            pass

    def fake_serve(_socket_path: Path, _worker: MainWorker, _lifecycle_path: Path, stop_event: threading.Event) -> None:
        assert not stop_event.is_set()
        signal.raise_signal(signal.SIGINT)
        assert stop_event.is_set()

    monkeypatch.setattr(direct_centauro_quantum_worker, "DirectCentauroQuantumWorker", MainWorker)
    monkeypatch.setattr(direct_centauro_quantum_worker, "serve", fake_serve)
    monkeypatch.setattr(sys, "argv", ["qc-worker", "--socket", str(tmp_path / "worker.sock"), "--lifecycle", str(tmp_path / "worker.lifecycle.json")])
    assert direct_centauro_quantum_worker.main() == 0
    assert "Worker stopped cleanly." in capsys.readouterr().out
