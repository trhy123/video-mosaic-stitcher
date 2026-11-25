import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import video_mosaic as mosaic


def ffmpeg_with_x264():
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        return False
    result = subprocess.run(['ffmpeg', '-hide_banner', '-encoders'], stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, check=False)
    return result.returncode == 0 and 'libx264' in result.stdout


class ExecutableSearchTests(unittest.TestCase):
    def test_local_then_bin_then_path_for_each_tool(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'bin').mkdir()
            extension = '.exe' if os.name == 'nt' else ''
            direct = root / ('ffmpeg' + extension)
            nested = root / 'bin' / ('ffmpeg' + extension)
            probe = root / 'bin' / ('ffprobe' + extension)
            for path in (direct, nested, probe):
                path.write_bytes(b'test')
                path.chmod(0o755)
            with (patch.object(mosaic, '__file__', str(root / 'video_mosaic.py')),
                  patch.object(mosaic.shutil, 'which', return_value='PATH-copy') as lookup):
                self.assertEqual(Path(mosaic.executable('ffmpeg')), direct.resolve())
                self.assertEqual(Path(mosaic.executable('ffprobe')), probe.resolve())
                lookup.assert_not_called()
                direct.unlink()
                self.assertEqual(Path(mosaic.executable('ffmpeg')), nested.resolve())
                nested.unlink()
                self.assertEqual(mosaic.executable('ffmpeg'), 'PATH-copy')

    def test_missing_error_mentions_all_locations(self):
        with tempfile.TemporaryDirectory() as folder:
            with (patch.object(mosaic, '__file__', str(Path(folder) / 'video_mosaic.py')),
                  patch.object(mosaic.shutil, 'which', return_value=None)):
                with self.assertRaisesRegex(ValueError, 'bin.*PATH'):
                    mosaic.executable('ffprobe')

    def test_frozen_application_uses_executable_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            extension = '.exe' if os.name == 'nt' else ''
            binary = root / ('ffprobe' + extension)
            binary.write_bytes(b'test'); binary.chmod(0o755)
            with (patch.object(mosaic.sys, 'frozen', True, create=True),
                  patch.object(mosaic.sys, 'executable', str(root / ('VideoMosaic' + extension))),
                  patch.object(mosaic.shutil, 'which', return_value=None)):
                self.assertEqual(Path(mosaic.executable('ffprobe')), binary.resolve())


class DimensionTests(unittest.TestCase):
    def test_recent_cell_size_controls_output(self):
        fields = {'cell_size': '640x360', 'width': '20', 'height': '20', 'ratio': '1:1'}
        result = mosaic.resolve_dimensions(fields, ['width', 'height', 'ratio', 'cell_size'], 2, 3)
        self.assertEqual(result, {'cell_size': '640×360', 'width': '1920', 'height': '720', 'ratio': '16:9'})


@unittest.skipUnless(ffmpeg_with_x264(), 'FFmpeg with libx264 and ffprobe is required')
class LoopTests(unittest.TestCase):
    def test_probe_reads_rotation_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / 'rotated.mp4'
            video.touch()
            data = {'streams': [{'codec_type': 'video', 'width': 64, 'height': 32,
                                 'duration': '1', 'avg_frame_rate': '5/1',
                                 'side_data_list': [{'rotation': 90}]}]}
            result = SimpleNamespace(returncode=0, stdout=json.dumps(data), stderr='')
            with patch.object(mosaic.subprocess, 'run', return_value=result):
                info = mosaic.probe(video)
            self.assertEqual((info['width'], info['height']), (32, 64))

    def test_shorter_video_repeats_until_longest_finishes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            short, long = root / 'short.mp4', root / 'long.mp4'
            for path, color, duration in ((short, 'red', 1), (long, 'blue', 3)):
                subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                                f'color={color}:size=64x64:rate=5:duration={duration}',
                                '-c:v', 'mpeg4', '-q:v', '3', str(path)], check=True)
            outputs = {}
            for mode in ('shortest', 'longest', 'loop'):
                output = root / (mode + '.mp4')
                mosaic.compose([short, long], output, 1, 2, 'libx264', 'ultrafast', 23,
                               '#000000', mode, log=lambda _: None)
                outputs[mode] = output
                expected = 1.0 if mode == 'shortest' else 3.0
                self.assertAlmostEqual(mosaic.probe(output)['duration'], expected, delta=0.05)
            def pixel(path, seconds):
                result = subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(seconds), '-i', str(path),
                                         '-vf', 'crop=2:2:30:30', '-frames:v', '1', '-f', 'rawvideo',
                                         '-pix_fmt', 'rgb24', '-'], stdout=subprocess.PIPE, check=True)
                return tuple(result.stdout[:3])
            self.assertLess(max(pixel(outputs['longest'], 1.5)), 15)
            for at in (1.5, 2.5):
                red, green, blue = pixel(outputs['loop'], at)
                self.assertGreater(red, 200)
                self.assertLess(green, 30)
                self.assertLess(blue, 30)
            encoder_list = subprocess.run(['ffmpeg', '-hide_banner', '-encoders'],
                                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                          text=True, check=True).stdout
            for encoder in ('libx264', 'libx265'):
                if encoder not in encoder_list:
                    continue
                output = root / (encoder + '-target.mp4')
                logs = []
                actual_run = subprocess.run
                stats_seen = []
                def check_stats(command, *args, **kwargs):
                    if 'pass=2:stats=stats.x265' in command:
                        stats = Path(kwargs['cwd']) / 'stats.x265'
                        self.assertGreater(stats.stat().st_size, 0)
                        stats_seen.append(True)
                    return actual_run(command, *args, **kwargs)
                with patch.object(mosaic.subprocess, 'run', side_effect=check_stats):
                    mosaic.compose([short, long], output, 1, 2, encoder, 'ultrafast', 23,
                                   '#000000', 'loop', log=logs.append, target_mb=0.1)
                self.assertAlmostEqual(mosaic.probe(output)['duration'], 3.0, delta=0.05)
                self.assertTrue(any('第 1/2' in line for line in logs))
                self.assertTrue(any('第 2/2' in line for line in logs))
                if encoder == 'libx265':
                    self.assertEqual(stats_seen, [True])


if __name__ == '__main__':
    unittest.main()
