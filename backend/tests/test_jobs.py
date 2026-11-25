import asyncio
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

from lyric_studio.jobs import SyncJobs, SyncOptions


class RunningWorker:
    def __init__(self, heartbeat):
        self.heartbeat = heartbeat

    def command(self, directory, _media, _input_format):
        child = """
from pathlib import Path
import sys
import time
heartbeat = Path(sys.argv[1])
with heartbeat.open("ab", buffering=0) as stream:
    while True:
        stream.write(b".")
        time.sleep(0.05)
"""
        worker = r"""
from pathlib import Path
import subprocess
import sys
child = subprocess.Popen([sys.executable, "-c", sys.argv[1], sys.argv[2]])
Path(sys.argv[3]).write_text("recognize\n", encoding="ascii")
child.wait()
"""
        return [sys.executable, "-c", worker, child, str(self.heartbeat), str(directory / "progress.txt")]


class SyncJobTests(unittest.IsolatedAsyncioTestCase):
    async def test_audit_selection_is_scoped_to_each_job(self):
        options = SyncOptions(Path("ffmpeg"), Path("small"), turbo_model=Path("turbo"),
                              demucs_python=Path("demucs"), audit_model=Path("medium"))
        registry = SyncJobs(options)
        self.addAsyncCleanup(registry.close)
        folder, media = Path("scratch"), Path("source.wav")
        plain = registry.register("wav", "歌詞", audit=False)
        self.assertIsNone(plain.options.audit_model)
        self.assertNotIn("--audit-model", plain.options.command(folder, media, "wav"))
        self.assertEqual(plain.options.profile, "refined")
        registry.cancel(plain)
        audited = registry.register("wav", "歌詞", audit=True)
        self.assertEqual(audited.options.command(folder, media, "wav")[-2:], ["--audit-model", "medium"])
        self.assertIsNone(plain.options.audit_model)

    async def test_cancel_stops_worker_and_child_before_releasing_slot(self):
        sandbox = self.enterContext(TemporaryDirectory())
        heartbeat = Path(sandbox) / "heartbeat"
        registry = SyncJobs(RunningWorker(heartbeat))
        self.addAsyncCleanup(registry.close)
        job = registry.register("wav", "歌詞")
        job.expiry.cancel()
        job.directory = TemporaryDirectory(dir=sandbox)
        root = Path(job.directory.name)
        job.state = "running"
        job.task = asyncio.create_task(registry.run(job, root / "source.wav", "wav"))
        async with asyncio.timeout(8):
            while not heartbeat.exists() or heartbeat.stat().st_size == 0:
                await asyncio.sleep(0.05)
        self.assertEqual(job.snapshot()["stage"], "原音の音声認識")
        with self.assertRaises(RuntimeError):
            registry.register("wav", "次の歌詞")
        registry.cancel(job)
        await asyncio.wait_for(asyncio.shield(job.task), timeout=10)
        self.assertEqual(job.state, "cancelled")
        self.assertFalse(root.exists())
        count = heartbeat.stat().st_size
        await asyncio.sleep(0.15)
        self.assertEqual(heartbeat.stat().st_size, count)
        next_job = registry.register("wav", "次の歌詞")
        registry.cancel(next_job)
        self.assertEqual(next_job.state, "cancelled")
