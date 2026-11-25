"""Local synchronization jobs with immutable inputs and temporary worker files."""

import asyncio
from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4


ACTIVE = {"queued", "uploading", "running", "cancelling"}
STAGES = {
    "decode": "音声の読み込み",
    "recognize": "原音の音声認識",
    "align": "認識結果と歌詞の照合",
    "separate": "ボーカル分離",
    "recognize_vocals": "分離したボーカルの音声認識",
    "recognize_turbo": "別モデルによる音声認識",
    "refine": "字幕候補の補正",
    "write": "字幕候補の生成",
}
LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class SyncOptions:
    ffmpeg: Path
    small_model: Path
    device: str = "cpu"
    turbo_model: Path | None = None
    demucs_python: Path | None = None

    @property
    def profile(self):
        return "refined" if self.turbo_model else "baseline"

    def command(self, directory, media, input_format):
        command = [
            sys.executable, "-m", "lyric_studio.cli", str(media), str(directory / "lyrics.txt"),
            "--small-model", str(self.small_model), "--ffmpeg", str(self.ffmpeg),
            "--device", self.device, "--input-format", input_format,
            "--output-dir", str(directory / "result"),
            "--progress-file", str(directory / "progress.txt"),
        ]
        if self.turbo_model:
            command += ["--turbo-model", str(self.turbo_model), "--demucs-python", str(self.demucs_python)]
        return command


@dataclass
class Job:
    id: str
    kind: str
    lyrics: str
    state: str = "queued"
    stage: str = "素材の転送待ち"
    error: str | None = None
    cues: list | None = None
    directory: TemporaryDirectory | None = None
    task: asyncio.Task | None = None
    expiry: asyncio.TimerHandle | None = None
    cancellation: asyncio.Event = field(default_factory=asyncio.Event)

    def snapshot(self):
        if self.state == "running" and self.directory:
            progress = Path(self.directory.name) / "progress.txt"
            try:
                stages = progress.read_text(encoding="ascii").splitlines()
            except OSError:
                stages = []
            for phase in stages:
                self.stage = STAGES.get(phase, self.stage)
        return {"id": self.id, "state": self.state, "stage": self.stage,
                "error": self.error, "cues": self.cues}


def stop_worker_tree(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        result = subprocess.run(
            ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        if result.returncode and process.poll() is None:
            raise RuntimeError("taskkill could not stop the worker tree")
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


class SyncJobs:
    def __init__(self, options):
        self.options = options
        self.jobs = {}

    def register(self, kind, lyrics):
        if any(job.state in ACTIVE for job in self.jobs.values()):
            raise RuntimeError("another job is active")
        if len(self.jobs) >= 12:
            del self.jobs[next(iter(self.jobs))]
        job = Job(uuid4().hex, kind, lyrics)
        self.jobs[job.id] = job
        job.expiry = asyncio.get_running_loop().call_later(60, self.expire, job)
        return job

    def expire(self, job):
        if job.state == "queued":
            self.finish(job, "failed", "素材の転送が始まりませんでした。再実行してください。")

    def cancel(self, job):
        if job.state == "queued":
            self.finish(job, "cancelled")
        elif job.state in {"uploading", "running"}:
            job.state = "cancelling"
            job.stage = "キャンセル中"
            job.cancellation.set()

    def finish(self, job, state, error=None):
        if job.expiry:
            job.expiry.cancel()
        if job.directory:
            try:
                job.directory.cleanup()
            except OSError:
                LOG.exception("Could not remove temporary synchronization files")
            job.directory = None
        job.lyrics = ""
        job.error = error
        job.stage = {"succeeded": "完了", "cancelled": "キャンセル済み", "failed": "失敗"}[state]
        job.state = state

    async def run(self, job, media, input_format):
        directory = media.parent
        exit_wait = None
        cancel_wait = None
        try:
            with (directory / "worker.log").open("wb") as log:
                process = subprocess.Popen(
                    self.options.command(directory, media, input_format),
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    start_new_session=os.name != "nt",
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            exit_wait = asyncio.create_task(asyncio.to_thread(process.wait))
            cancel_wait = asyncio.create_task(job.cancellation.wait())
            await asyncio.wait((exit_wait, cancel_wait), return_when=asyncio.FIRST_COMPLETED)
            if job.cancellation.is_set():
                await asyncio.to_thread(stop_worker_tree, process)
                await exit_wait
                state, error = "cancelled", None
            elif exit_wait.result():
                stage = job.snapshot()["stage"]
                state, error = "failed", f"{stage}に失敗しました（終了コード {exit_wait.result()}）。"
            else:
                result = json.loads((directory / "result" / "sync.json").read_text(encoding="utf-8"))
                job.cues = result["lines"]
                state, error = "succeeded", None
        except (OSError, ValueError, KeyError, RuntimeError):
            LOG.exception("Synchronization worker failed")
            if exit_wait and not exit_wait.done():
                await exit_wait
            state, error = "failed", "同期処理を完了できませんでした。起動設定と素材を確認してください。"
        finally:
            if cancel_wait:
                cancel_wait.cancel()
        self.finish(job, state, error)

    async def close(self):
        tasks = []
        for job in self.jobs.values():
            self.cancel(job)
            if job.task:
                tasks.append(job.task)
        await asyncio.gather(*tasks)
