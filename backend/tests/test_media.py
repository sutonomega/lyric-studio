import struct
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import wave

from lyric_studio.media import waveform_peaks


class WaveformTests(unittest.TestCase):
    def test_peaks_keep_stereo_signal_and_cover_the_full_duration(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "stereo.wav"
            with wave.open(str(path), "wb") as output:
                output.setnchannels(2)
                output.setsampwidth(2)
                output.setframerate(1000)
                output.writeframes(struct.pack("<10h", 0, 0, -32768, 32767, 0, 0, 0, 0, 16384, -16384))
            self.assertEqual(waveform_peaks(path, bins=2), {"duration_ms": 5, "peaks": [1.0, 0.5]})
