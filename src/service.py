"""Authenticated HTTP service for programmatic AccelaCLI downloads."""

from __future__ import annotations

import json
import logging
import secrets
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from core.tasks.download_depots_task import DownloadDepotsTask

logger = logging.getLogger(__name__)
MAX_BODY_BYTES = 1 * 1024 * 1024


class Job:
    def __init__(self, job_id: str, request: dict[str, Any]) -> None:
        self.id = job_id
        self.request = request
        self.status = "queued"
        self.exit_code: int | None = None
        self.error: str | None = None
        self.events: list[dict[str, Any]] = []
        self.task: DownloadDepotsTask | None = None
        self.lock = threading.Lock()

    def add_event(self, event: dict[str, Any]) -> None:
        with self.lock:
            self.events.append(event)
            if len(self.events) > 500:
                del self.events[:-500]

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "id": self.id,
                "status": self.status,
                "exit_code": self.exit_code,
                "error": self.error,
                "request": self.request,
                "events": list(self.events),
            }


class JobManager:
    def __init__(self, logger_: logging.Logger) -> None:
        self.logger = logger_
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()
        self.queue: list[Job] = []
        self.condition = threading.Condition(self.lock)
        self.worker = threading.Thread(target=self._worker, daemon=True)
        self.worker.start()

    def submit(self, request: dict[str, Any]) -> Job:
        job = Job(uuid.uuid4().hex, request)
        with self.condition:
            self.jobs[job.id] = job
            self.queue.append(job)
            self.condition.notify()
        return job

    def get(self, job_id: str) -> Job | None:
        with self.lock:
            return self.jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None:
            return False
        with job.lock:
            if job.status == "queued":
                job.status = "cancelled"
                return True
            if job.status == "running" and job.task is not None:
                job.task.stop()
                job.status = "cancelling"
                return True
        return False

    def cancel_all(self) -> None:
        with self.lock:
            job_ids = list(self.jobs)
        for job_id in job_ids:
            self.cancel(job_id)

    def _worker(self) -> None:
        while True:
            with self.condition:
                while not self.queue:
                    self.condition.wait()
                job = self.queue.pop(0)
            with job.lock:
                if job.status == "cancelled":
                    continue
                job.status = "running"
            try:
                from cmd_main import run_download

                args = _request_to_namespace(job.request)
                job.add_event({"event": "job_started", "job_id": job.id})
                code = run_download(
                    args,
                    self.logger,
                    event_sink=lambda event: job.add_event(
                        {**event, "job_id": job.id}
                    ),
                    task_sink=lambda task: self._set_task(job, task),
                )
                with job.lock:
                    job.exit_code = code
                    job.status = "cancelled" if job.status == "cancelling" else (
                        "completed" if code == 0 else "failed"
                    )
            except Exception as exc:
                self.logger.error("Service job %s failed: %s", job.id, exc, exc_info=True)
                job.add_event({"event": "error", "error": str(exc), "job_id": job.id})
                with job.lock:
                    job.error = str(exc)
                    job.exit_code = 1
                    job.status = "failed"

    @staticmethod
    def _set_task(job: Job, task: DownloadDepotsTask) -> None:
        with job.lock:
            job.task = task


def _request_to_namespace(request: dict[str, Any]):
    import argparse
    import sys

    required = ("destination",)
    if any(key not in request for key in required):
        raise ValueError("Request requires destination")
    if bool(request.get("appid")) == bool(request.get("manifest")):
        raise ValueError("Request must provide exactly one of appid or manifest")
    allowed = {
        "appid", "manifest", "destination", "depots", "dlcs", "max_downloads",
        "goldberg", "steamless", "eos_proxy", "netsock", "branch", "platform",
        "config",
    }
    unknown = set(request) - allowed
    if unknown:
        raise ValueError(f"Unknown request field(s): {', '.join(sorted(unknown))}")
    return argparse.Namespace(
        command="download",
        appid=int(request["appid"]) if request.get("appid") is not None else None,
        manifest=Path(request["manifest"]) if request.get("manifest") else None,
        destination=Path(request["destination"]),
        depots=[str(item) for item in request.get("depots", [])] or None,
        dlcs=[str(item) for item in request.get("dlcs", [])] or None,
        max_downloads=int(request.get("max_downloads", 8)),
        auto_apply_goldberg=request.get("goldberg"),
        use_steamless=request.get("steamless"),
        enable_eosproxy_default=request.get("eos_proxy"),
        enable_netsock_default=request.get("netsock"),
        branch=request.get("branch"),
        platform=request.get(
            "platform", "windows" if sys.platform == "win32" else sys.platform
        ),
        json=False,
        config=Path(request["config"]) if request.get("config") else None,
    )


class ServiceHandler(BaseHTTPRequestHandler):
    server: "ServiceServer"

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("%s - - %s", self.address_string(), format % args)

    def _authorized(self) -> bool:
        provided = self.headers.get("X-AccelaCLI-Token", "")
        return secrets.compare_digest(provided, self.server.token)

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > MAX_BODY_BYTES:
            raise ValueError("Request body too large")
        value = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(value, dict):
            raise ValueError("Request body must be a JSON object")
        return value

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, {"status": "ok"})
            return
        if not self._authorized():
            self._send(401, {"error": "Unauthorized"})
            return
        parts = urlparse(self.path).path.strip("/").split("/")
        if len(parts) == 2 and parts[0] == "jobs":
            job = self.server.jobs.get(parts[1])
            if job is None:
                self._send(404, {"error": "Job not found"})
            else:
                self._send(200, job.snapshot())
            return
        self._send(404, {"error": "Not found"})

    def do_POST(self) -> None:
        if not self._authorized():
            self._send(401, {"error": "Unauthorized"})
            return
        parts = urlparse(self.path).path.strip("/").split("/")
        try:
            if self.path == "/jobs":
                job = self.server.jobs.submit(self._body())
                self._send(202, {"id": job.id, "status": job.status})
                return
            if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "cancel":
                if not self.server.jobs.cancel(parts[1]):
                    self._send(409, {"error": "Job cannot be cancelled"})
                else:
                    self._send(202, {"id": parts[1], "status": "cancelling"})
                return
            self._send(404, {"error": "Not found"})
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            self._send(400, {"error": str(exc)})


class ServiceServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], token: str, logger_: logging.Logger):
        self.token = token
        self.jobs = JobManager(logger_)
        super().__init__(address, ServiceHandler)


def run_service(args: Any, logger_: logging.Logger) -> int:
    token = args.token or secrets.token_urlsafe(32)
    server = ServiceServer((args.host, args.port), token, logger_)
    print(f"AccelaCLI service listening on http://{args.host}:{args.port}", flush=True)
    print(f"Authentication token: {token}", flush=True)
    logger_.info("Service listening on %s:%s", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger_.info("Stopping service")
    finally:
        server.jobs.cancel_all()
        server.server_close()
    return 0


