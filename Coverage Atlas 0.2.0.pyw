from __future__ import annotations

import os
import sys
import re
import json
import math
import hashlib
import threading
import subprocess
import time
import traceback
import platform
import shutil
import importlib
from pathlib import Path
from dataclasses import dataclass, field
from collections import defaultdict, Counter
from difflib import SequenceMatcher
from concurrent.futures import ThreadPoolExecutor, as_completed

APP = 'Coverage Atlas'
VERSION = '0.2.0'
STATUS = 'Under construction ⚠️'
MATCH_DURATION_TOLERANCE = 2.0
EXACT_SEARCH_RELEASE_LIMIT = 70
LEARNED_POLICY_VERSION = '1.5.1'


def docs_dir() -> Path:
    if os.name == 'nt':
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders',
            ) as key:
                value, _ = winreg.QueryValueEx(key, 'Personal')
            value = os.path.expandvars(str(value))
            if value:
                return Path(value)
        except Exception:
            pass
    return Path.home() / 'Documents'


DATA = docs_dir() / 'Karpuzikov Tools' / APP
for _name in ('logs', 'cache', 'temp', 'dependencies'):
    (DATA / _name).mkdir(parents=True, exist_ok=True)
DEPS = DATA / 'dependencies'
SETTINGS = DATA / 'settings.json'
if str(DEPS) not in sys.path:
    sys.path.insert(0, str(DEPS))


class Diagnostics:
    def __init__(self):
        self.lock = threading.RLock()
        stamp = time.strftime('%Y-%m-%d-%H-%M-%S')
        self.path = DATA / 'logs' / f'{APP} Analysis {stamp}.jsonl'
        self._fh = None
        try:
            self._fh = self.path.open('a', encoding='utf-8', buffering=1)
        except Exception:
            self._fh = None
        self.event(
            'app_start',
            python=sys.version.split()[0],
            platform=platform.platform(),
            executable=sys.executable,
            data_dir=str(DATA),
        )

    def event(self, event: str, **data):
        if not self._fh:
            return
        row = {
            'ts': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'event': event,
            'app': APP,
            'version': VERSION,
        }
        row.update(data)
        try:
            line = json.dumps(row, ensure_ascii=False, separators=(',', ':'), default=str)
            with self.lock:
                self._fh.write(line + '\n')
                self._fh.flush()
        except Exception:
            pass

    def error(self, event: str, exc: BaseException, **data):
        data.update(
            error_type=type(exc).__name__,
            error=str(exc),
            traceback=traceback.format_exc(),
        )
        self.event(event, **data)

    def close(self):
        try:
            self.event('app_exit')
            if self._fh:
                self._fh.close()
        except Exception:
            pass


LOG = Diagnostics()


def ensure_winget():
    if os.name != 'nt' or shutil.which('winget'):
        return
    LOG.event('winget_missing')
    ps = shutil.which('powershell') or shutil.which('pwsh')
    if not ps:
        LOG.event('winget_install_skipped', reason='powershell_not_found')
        return
    bundle = str(DATA / 'temp' / 'Microsoft.DesktopAppInstaller.msixbundle')
    cmd = [
        ps,
        '-NoProfile',
        '-ExecutionPolicy',
        'Bypass',
        '-Command',
        f"$ProgressPreference='SilentlyContinue'; "
        f"Invoke-WebRequest -UseBasicParsing 'https://aka.ms/getwinget' -OutFile '{bundle}'; "
        f"Add-AppxPackage '{bundle}'",
    ]
    try:
        subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=0x08000000,
        )
        LOG.event('winget_install_complete')
    except Exception as exc:
        LOG.error('winget_install_failed', exc)


def ensure_mutagen():
    try:
        spec = importlib.util.find_spec('mutagen')
        if spec and spec.origin and DEPS.resolve() in Path(spec.origin).resolve().parents:
            return
    except Exception:
        pass

    LOG.event('dependency_install_start', dependency='mutagen', target=str(DEPS))
    try:
        subprocess.check_call(
            [
                sys.executable, '-m', 'pip', 'install', '--upgrade',
                '--target', str(DEPS), 'mutagen>=1.47,<2'
            ],
            creationflags=(0x08000000 if os.name == 'nt' else 0),
        )
        importlib.invalidate_caches()
        LOG.event('dependency_install_complete', dependency='mutagen', target=str(DEPS))
    except Exception as exc:
        LOG.error('dependency_install_failed', exc, dependency='mutagen', target=str(DEPS))
        raise


ensure_winget()
ensure_mutagen()

from mutagen import File as MFile


# ---------------------------------------------------------------------------
# PMF / TT Dynamic Range measurement
#
# Compatible implementation derived from the publicly documented TT-DR method
# and the MIT-licensed MacinMeter Candidate V1 profile, which targets
# foo_dr_meter 1.0.8 (a modern implementation tuned closer to DROffline MkII).
# The proprietary DROffline executable is NOT bundled or executed.
# ---------------------------------------------------------------------------
DR_ALGORITHM_REVISION = 'tt-dr-macinmeter-candidate-v1'
DR_WINDOW_COEFFICIENT = 3.0040816326530613
DR_CACHE_FILE = DATA / 'cache' / 'dynamic-range-v1.json'
DR_WORKERS = 4
_DR_CACHE_LOCK = threading.RLock()
_FFMPEG_PATH = None
_FFMPEG_SEARCHED = False


def hidden_subprocess_kwargs() -> dict:
    if os.name != 'nt':
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {
        'creationflags': subprocess.CREATE_NO_WINDOW,
        'startupinfo': startupinfo,
    }


def find_ffmpeg() -> str | None:
    global _FFMPEG_PATH, _FFMPEG_SEARCHED
    if _FFMPEG_SEARCHED:
        return _FFMPEG_PATH
    _FFMPEG_SEARCHED = True

    for name in ('ffmpeg.exe', 'ffmpeg'):
        found = shutil.which(name)
        if found:
            _FFMPEG_PATH = found
            return found

    if os.name == 'nt':
        roots = []
        local = os.environ.get('LOCALAPPDATA')
        if local:
            roots.append(Path(local) / 'Microsoft' / 'WinGet' / 'Packages')
        program_files = os.environ.get('ProgramFiles')
        if program_files:
            roots.append(Path(program_files))
        for root in roots:
            if not root.is_dir():
                continue
            try:
                for candidate in root.rglob('ffmpeg.exe'):
                    if candidate.is_file():
                        _FFMPEG_PATH = str(candidate)
                        return _FFMPEG_PATH
            except Exception:
                pass
    return None


def ffprobe_for(ffmpeg: str) -> str | None:
    ffmpeg_path = Path(ffmpeg)
    name = 'ffprobe.exe' if os.name == 'nt' else 'ffprobe'
    sibling = ffmpeg_path.with_name(name)
    if sibling.is_file():
        return str(sibling)
    return shutil.which(name)


def read_dr_cache() -> dict:
    try:
        data = json.loads(DR_CACHE_FILE.read_text('utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_dr_cache(data: dict):
    try:
        DR_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp = DR_CACHE_FILE.with_suffix('.tmp')
        temp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        temp.replace(DR_CACHE_FILE)
    except Exception as exc:
        LOG.error('dynamic_range_cache_write_error', exc)


def _round_half_away(value: float) -> int:
    return int(math.floor(value + 0.5)) if value >= 0 else int(math.ceil(value - 0.5))


def _finite_level(text: str) -> float | None:
    try:
        value = float(text.strip())
        return value if math.isfinite(value) else None
    except Exception:
        return None


def _dr_from_channel_windows(windows: list[tuple[float | None, float | None]]) -> float | None:
    if not windows:
        return None

    histogram = [0] * 10001
    peaks = []
    saw_nonzero = False

    for rms_dbfs, peak_dbfs in windows:
        if rms_dbfs is not None:
            # TT-DR uses sqrt(2 * mean(square)); FFmpeg astats reports ordinary
            # RMS, so add 20*log10(sqrt(2)) = 3.0102999566 dB.
            dr_rms_db = rms_dbfs + 3.010299956639812
            key = max(-10000, min(0, _round_half_away(dr_rms_db * 100.0)))
            histogram[key + 10000] += 1

        if peak_dbfs is not None:
            saw_nonzero = True
            peaks.append((peak_dbfs, _round_half_away(peak_dbfs * 100.0)))

    if not saw_nonzero:
        return 0.0

    primary = None
    secondary = None
    for candidate in peaks:
        if primary is None:
            primary = candidate
        elif candidate[1] > primary[1]:
            secondary = primary
            primary = candidate
        elif secondary is None or candidate[1] > secondary[1]:
            secondary = candidate

    if primary is None:
        return 0.0

    target = max(len(windows) // 5, 1)
    selected_count = 0
    selected_power = 0.0
    for bin_index in range(10000, -1, -1):
        count = histogram[bin_index]
        if not count:
            continue
        bin_db = -100.0 + bin_index * 0.01
        selected_count += count
        selected_power += (10.0 ** (bin_db / 10.0)) * count
        if selected_count >= target:
            break

    if selected_count <= 0 or selected_power <= 0.0:
        return 0.0

    loud_rms_db = 10.0 * math.log10(selected_power / selected_count)
    selected_peak_db = secondary[0] if secondary is not None else primary[0]
    dr = selected_peak_db - loud_rms_db
    if dr < 0.0:
        dr = max(0.0, primary[0] - loud_rms_db)
    return float(dr)


def probe_audio_stream(path: Path, sample_rate: int, channels: int, ffmpeg: str) -> tuple[int, int]:
    if sample_rate > 0 and channels > 0:
        return sample_rate, channels

    ffprobe = ffprobe_for(ffmpeg)
    if not ffprobe:
        return sample_rate, channels

    try:
        completed = subprocess.run(
            [
                ffprobe, '-v', 'error', '-select_streams', 'a:0',
                '-show_entries', 'stream=sample_rate,channels',
                '-of', 'json', str(path),
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            **hidden_subprocess_kwargs(),
        )
        payload = json.loads(completed.stdout.decode('utf-8', 'replace'))
        streams = payload.get('streams') or []
        if streams:
            stream = streams[0]
            sample_rate = int(stream.get('sample_rate') or sample_rate or 0)
            channels = int(stream.get('channels') or channels or 0)
    except Exception:
        pass
    return sample_rate, channels


def analyze_dynamic_range_file(path: Path, sample_rate: int = 0, channels: int = 0) -> dict:
    try:
        stat = path.stat()
    except Exception as exc:
        return {'dr': None, 'dr_db': None, 'error': str(exc)}

    key = str(path.resolve()).casefold()
    with _DR_CACHE_LOCK:
        cache = read_dr_cache()
        old = cache.get(key, {})
        if (
            old.get('size') == stat.st_size and
            old.get('mtime_ns') == stat.st_mtime_ns and
            old.get('algorithm') == DR_ALGORITHM_REVISION and
            old.get('dr') is not None
        ):
            return dict(old)

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return {
            'dr': None,
            'dr_db': None,
            'error': 'ffmpeg not found',
            'algorithm': DR_ALGORITHM_REVISION,
        }

    sample_rate, channels = probe_audio_stream(path, sample_rate, channels, ffmpeg)
    if sample_rate <= 0 or channels <= 0:
        return {
            'dr': None,
            'dr_db': None,
            'error': 'Could not determine sample rate/channels',
            'algorithm': DR_ALGORITHM_REVISION,
        }

    window_frames = max(1, int(math.floor(sample_rate * DR_WINDOW_COEFFICIENT)))
    filter_graph = (
        f'asetnsamples=n={window_frames}:p=0,'
        'astats=metadata=1:reset=1,'
        'ametadata=print:file=-'
    )

    try:
        completed = subprocess.run(
            [
                ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-xerror',
                '-i', str(path), '-map', '0:a:0',
                '-af', filter_graph,
                '-f', 'null', '-',
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **hidden_subprocess_kwargs(),
        )

        if completed.returncode != 0:
            error = completed.stderr.decode('utf-8', 'replace').strip()
            return {
                'dr': None,
                'dr_db': None,
                'error': error or f'ffmpeg exited with code {completed.returncode}',
                'algorithm': DR_ALGORITHM_REVISION,
            }

        per_channel = {index: [] for index in range(1, channels + 1)}
        current = {}

        def flush_current():
            if not current:
                return
            for index in range(1, channels + 1):
                values = current.get(index, {})
                per_channel[index].append(
                    (values.get('rms'), values.get('peak'))
                )
            current.clear()

        rms_re = re.compile(r'^lavfi\.astats\.(\d+)\.RMS_level=(.+)$')
        peak_re = re.compile(r'^lavfi\.astats\.(\d+)\.Peak_level=(.+)$')

        for raw_line in completed.stdout.decode('utf-8', 'replace').splitlines():
            line = raw_line.strip()
            if line.startswith('frame:'):
                flush_current()
                continue

            match = rms_re.match(line)
            if match:
                index = int(match.group(1))
                if 1 <= index <= channels:
                    current.setdefault(index, {})['rms'] = _finite_level(match.group(2))
                continue

            match = peak_re.match(line)
            if match:
                index = int(match.group(1))
                if 1 <= index <= channels:
                    current.setdefault(index, {})['peak'] = _finite_level(match.group(2))

        flush_current()

        channel_drs = []
        for index in range(1, channels + 1):
            value = _dr_from_channel_windows(per_channel[index])
            if value is not None:
                channel_drs.append(value)

        if not channel_drs:
            return {
                'dr': None,
                'dr_db': None,
                'error': 'Insufficient decoded audio',
                'algorithm': DR_ALGORITHM_REVISION,
            }

        dr_db = sum(channel_drs) / len(channel_drs)
        result = {
            'dr': int(math.floor(dr_db + 0.5)),
            'dr_db': round(dr_db, 4),
            'sample_rate': sample_rate,
            'channels': channels,
            'channel_dr_db': [round(value, 4) for value in channel_drs],
            'size': stat.st_size,
            'mtime_ns': stat.st_mtime_ns,
            'algorithm': DR_ALGORITHM_REVISION,
            'error': '',
        }
        with _DR_CACHE_LOCK:
            cache = read_dr_cache()
            cache[key] = result
            write_dr_cache(cache)
        return result
    except Exception as exc:
        LOG.error('dynamic_range_file_error', exc, path=str(path))
        return {
            'dr': None,
            'dr_db': None,
            'error': f'{type(exc).__name__}: {exc}',
            'algorithm': DR_ALGORITHM_REVISION,
        }


def measure_dynamic_range_tracks(tracks, progress=lambda done, total: None):
    if not tracks:
        return

    def worker(track):
        result = analyze_dynamic_range_file(
            Path(track.path),
            sample_rate=track.sample_rate,
            channels=track.channels,
        )
        return track, result

    completed = 0
    with ThreadPoolExecutor(max_workers=DR_WORKERS, thread_name_prefix='CoverageAtlasDR') as pool:
        futures = [pool.submit(worker, track) for track in tracks]
        for future in as_completed(futures):
            track, result = future.result()
            track.dynamic_range = result.get('dr')
            track.dynamic_range_db = result.get('dr_db')
            track.dynamic_range_error = str(result.get('error') or '')
            completed += 1
            progress(completed, len(tracks))
            LOG.event(
                'dynamic_range_track',
                relative_path=track.relative_path,
                dr=track.dynamic_range,
                dr_db=track.dynamic_range_db,
                error=track.dynamic_range_error,
            )


def finalize_release_dynamic_range(release):
    measured = [track for track in release.tracks if track.dynamic_range_db is not None]
    release.dynamic_range_measured_tracks = len(measured)
    release.dynamic_range_complete = bool(release.tracks) and len(measured) == len(release.tracks)

    if not release.dynamic_range_complete:
        release.dynamic_range = None
        release.dynamic_range_db = None
        return

    dr_db = sum(float(track.dynamic_range_db) for track in measured) / len(measured)
    release.dynamic_range_db = round(dr_db, 4)
    release.dynamic_range = int(math.floor(dr_db + 0.5))


def dynamic_range_display(release) -> str:
    if release.dynamic_range is not None:
        return (
            f'DR{release.dynamic_range} '
            f'({release.dynamic_range_measured_tracks}/{len(release.tracks)} tracks)'
        )
    if not release.dynamic_range_attempted:
        return 'not measured'
    if release.dynamic_range_measured_tracks:
        return (
            f'partial '
            f'({release.dynamic_range_measured_tracks}/{len(release.tracks)} tracks)'
        )
    if not find_ffmpeg():
        return 'unavailable (ffmpeg not found)'
    return 'unavailable'


# Vendored ligh7s/hey-bro-check-log 1.3.2 (d3192ad2764f2682cffce4db2abc419f8ac68c69)
# Apache-2.0; upstream LICENSE is embedded in VENDORED_HEYBRO_FILES.
# No runtime download/install is used.
HEYBROCHECKLOG_PACKAGE_VERSION = '1.3.2'
HEYBROCHECKLOG_COMMIT = 'd3192ad2764f2682cffce4db2abc419f8ac68c69'
HEYBROCHECKLOG_BUNDLE_REVISION = 'embedded-v3'
VENDORED_HEYBRO_FILES = {"heybrochecklog/__init__.py":"# Vendored from ligh7s/hey-bro-check-log d3192ad2764f2682cffce4db2abc419f8ac68c69\nclass UnrecognizedException(Exception):\n    pass\n","heybrochecklog/shared.py":"\"\"\"Shared helpers adapted from ligh7s/hey-bro-check-log for embedded use.\"\"\"\nimport codecs\nimport json\nimport os\n\ndef get_log_contents(log_file):\n    encoding = get_log_encoding(log_file)\n    with log_file.open(encoding=encoding) as log:\n        return log.readlines()\n\ndef get_log_encoding(log_file):\n    raw = log_file.read_bytes()\n    if raw.startswith(codecs.BOM_UTF8):\n        return 'utf-8-sig'\n    if raw.startswith(codecs.BOM_UTF16_LE) or raw.startswith(codecs.BOM_UTF16_BE):\n        return 'utf-16'\n    try:\n        raw.decode('utf-8')\n        return 'utf-8'\n    except UnicodeDecodeError:\n        pass\n    try:\n        decoded = raw.decode('cp1251')\n        letters = [ch for ch in decoded if ch.isalpha()]\n        cyrillic = [ch for ch in letters if '\\u0400' <= ch <= '\\u04ff']\n        if letters and len(cyrillic) / len(letters) >= 0.15:\n            return 'cp1251'\n    except UnicodeDecodeError:\n        pass\n    return 'cp1252'\n\ndef format_pattern(pattern, append=None):\n    if append:\n        pattern = [p + append for p in pattern]\n    return '|'.join(pattern)\n\ndef open_json(*paths):\n    basepath = get_path()\n    with open(os.path.join(basepath, 'resources', *paths), encoding='utf-8') as jsonfile:\n        return json.load(jsonfile)\n\ndef get_path():\n    return os.path.abspath(os.path.dirname(__file__))\n","LICENSE":"                                 Apache License\n                           Version 2.0, January 2004\n                        http://www.apache.org/licenses/\n\n   TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION\n\n   1. Definitions.\n\n      \"License\" shall mean the terms and conditions for use, reproduction,\n      and distribution as defined by Sections 1 through 9 of this document.\n\n      \"Licensor\" shall mean the copyright owner or entity authorized by\n      the copyright owner that is granting the License.\n\n      \"Legal Entity\" shall mean the union of the acting entity and all\n      other entities that control, are controlled by, or are under common\n      control with that entity. For the purposes of this definition,\n      \"control\" means (i) the power, direct or indirect, to cause the\n      direction or management of such entity, whether by contract or\n      otherwise, or (ii) ownership of fifty percent (50%) or more of the\n      outstanding shares, or (iii) beneficial ownership of such entity.\n\n      \"You\" (or \"Your\") shall mean an individual or Legal Entity\n      exercising permissions granted by this License.\n\n      \"Source\" form shall mean the preferred form for making modifications,\n      including but not limited to software source code, documentation\n      source, and configuration files.\n\n      \"Object\" form shall mean any form resulting from mechanical\n      transformation or translation of a Source form, including but\n      not limited to compiled object code, generated documentation,\n      and conversions to other media types.\n\n      \"Work\" shall mean the work of authorship, whether in Source or\n      Object form, made available under the License, as indicated by a\n      copyright notice that is included in or attached to the work\n      (an example is provided in the Appendix below).\n\n      \"Derivative Works\" shall mean any work, whether in Source or Object\n      form, that is based on (or derived from) the Work and for which the\n      editorial revisions, annotations, elaborations, or other modifications\n      represent, as a whole, an original work of authorship. For the purposes\n      of this License, Derivative Works shall not include works that remain\n      separable from, or merely link (or bind by name) to the interfaces of,\n      the Work and Derivative Works thereof.\n\n      \"Contribution\" shall mean any work of authorship, including\n      the original version of the Work and any modifications or additions\n      to that Work or Derivative Works thereof, that is intentionally\n      submitted to Licensor for inclusion in the Work by the copyright owner\n      or by an individual or Legal Entity authorized to submit on behalf of\n      the copyright owner. For the purposes of this definition, \"submitted\"\n      means any form of electronic, verbal, or written communication sent\n      to the Licensor or its representatives, including but not limited to\n      communication on electronic mailing lists, source code control systems,\n      and issue tracking systems that are managed by, or on behalf of, the\n      Licensor for the purpose of discussing and improving the Work, but\n      excluding communication that is conspicuously marked or otherwise\n      designated in writing by the copyright owner as \"Not a Contribution.\"\n\n      \"Contributor\" shall mean Licensor and any individual or Legal Entity\n      on behalf of whom a Contribution has been received by Licensor and\n      subsequently incorporated within the Work.\n\n   2. Grant of Copyright License. Subject to the terms and conditions of\n      this License, each Contributor hereby grants to You a perpetual,\n      worldwide, non-exclusive, no-charge, royalty-free, irrevocable\n      copyright license to reproduce, prepare Derivative Works of,\n      publicly display, publicly perform, sublicense, and distribute the\n      Work and such Derivative Works in Source or Object form.\n\n   3. Grant of Patent License. Subject to the terms and conditions of\n      this License, each Contributor hereby grants to You a perpetual,\n      worldwide, non-exclusive, no-charge, royalty-free, irrevocable\n      (except as stated in this section) patent license to make, have made,\n      use, offer to sell, sell, import, and otherwise transfer the Work,\n      where such license applies only to those patent claims licensable\n      by such Contributor that are necessarily infringed by their\n      Contribution(s) alone or by combination of their Contribution(s)\n      with the Work to which such Contribution(s) was submitted. If You\n      institute patent litigation against any entity (including a\n      cross-claim or counterclaim in a lawsuit) alleging that the Work\n      or a Contribution incorporated within the Work constitutes direct\n      or contributory patent infringement, then any patent licenses\n      granted to You under this License for that Work shall terminate\n      as of the date such litigation is filed.\n\n   4. Redistribution. You may reproduce and distribute copies of the\n      Work or Derivative Works thereof in any medium, with or without\n      modifications, and in Source or Object form, provided that You\n      meet the following conditions:\n\n      (a) You must give any other recipients of the Work or\n          Derivative Works a copy of this License; and\n\n      (b) You must cause any modified files to carry prominent notices\n          stating that You changed the files; and\n\n      (c) You must retain, in the Source form of any Derivative Works\n          that You distribute, all copyright, patent, trademark, and\n          attribution notices from the Source form of the Work,\n          excluding those notices that do not pertain to any part of\n          the Derivative Works; and\n\n      (d) If the Work includes a \"NOTICE\" text file as part of its\n          distribution, then any Derivative Works that You distribute must\n          include a readable copy of the attribution notices contained\n          within such NOTICE file, excluding those notices that do not\n          pertain to any part of the Derivative Works, in at least one\n          of the following places: within a NOTICE text file distributed\n          as part of the Derivative Works; within the Source form or\n          documentation, if provided along with the Derivative Works; or,\n          within a display generated by the Derivative Works, if and\n          wherever such third-party notices normally appear. The contents\n          of the NOTICE file are for informational purposes only and\n          do not modify the License. You may add Your own attribution\n          notices within Derivative Works that You distribute, alongside\n          or as an addendum to the NOTICE text from the Work, provided\n          that such additional attribution notices cannot be construed\n          as modifying the License.\n\n      You may add Your own copyright statement to Your modifications and\n      may provide additional or different license terms and conditions\n      for use, reproduction, or distribution of Your modifications, or\n      for any such Derivative Works as a whole, provided Your use,\n      reproduction, and distribution of the Work otherwise complies with\n      the conditions stated in this License.\n\n   5. Submission of Contributions. Unless You explicitly state otherwise,\n      any Contribution intentionally submitted for inclusion in the Work\n      by You to the Licensor shall be under the terms and conditions of\n      this License, without any additional terms or conditions.\n      Notwithstanding the above, nothing herein shall supersede or modify\n      the terms of any separate license agreement you may have executed\n      with Licensor regarding such Contributions.\n\n   6. Trademarks. This License does not grant permission to use the trade\n      names, trademarks, service marks, or product names of the Licensor,\n      except as required for reasonable and customary use in describing the\n      origin of the Work and reproducing the content of the NOTICE file.\n\n   7. Disclaimer of Warranty. Unless required by applicable law or\n      agreed to in writing, Licensor provides the Work (and each\n      Contributor provides its Contributions) on an \"AS IS\" BASIS,\n      WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or\n      implied, including, without limitation, any warranties or conditions\n      of TITLE, NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A\n      PARTICULAR PURPOSE. You are solely responsible for determining the\n      appropriateness of using or redistributing the Work and assume any\n      risks associated with Your exercise of permissions under this License.\n\n   8. Limitation of Liability. In no event and under no legal theory,\n      whether in tort (including negligence), contract, or otherwise,\n      unless required by applicable law (such as deliberate and grossly\n      negligent acts) or agreed to in writing, shall any Contributor be\n      liable to You for damages, including any direct, indirect, special,\n      incidental, or consequential damages of any character arising as a\n      result of this License or out of the use or inability to use the\n      Work (including but not limited to damages for loss of goodwill,\n      work stoppage, computer failure or malfunction, or any and all\n      other commercial damages or losses), even if such Contributor\n      has been advised of the possibility of such damages.\n\n   9. Accepting Warranty or Additional Liability. While redistributing\n      the Work or Derivative Works thereof, You may choose to offer,\n      and charge a fee for, acceptance of support, warranty, indemnity,\n      or other liability obligations and/or rights consistent with this\n      License. However, in accepting such obligations, You may act only\n      on Your own behalf and on Your sole responsibility, not on behalf\n      of any other Contributor, and only if You agree to indemnify,\n      defend, and hold each Contributor harmless for any liability\n      incurred by, or claims asserted against, such Contributor by reason\n      of your accepting any such warranty or additional liability.\n\n   END OF TERMS AND CONDITIONS\n\n   APPENDIX: How to apply the Apache License to your work.\n\n      To apply the Apache License to your work, attach the following\n      boilerplate notice, with the fields enclosed by brackets \"[]\"\n      replaced with your own identifying information. (Don't include\n      the brackets!)  The text should be enclosed in the appropriate\n      comment syntax for the file format. We also recommend that a\n      file or class name and description of purpose be included on the\n      same \"printed page\" as the copyright notice for easier\n      identification within third-party archives.\n\n   Copyright [yyyy] [name of copyright owner]\n\n   Licensed under the Apache License, Version 2.0 (the \"License\");\n   you may not use this file except in compliance with the License.\n   You may obtain a copy of the License at\n\n       http://www.apache.org/licenses/LICENSE-2.0\n\n   Unless required by applicable law or agreed to in writing, software\n   distributed under the License is distributed on an \"AS IS\" BASIS,\n   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.\n   See the License for the specific language governing permissions and\n   limitations under the License.\n","heybrochecklog/analyze.py":"\"\"\"\nThis module analyzes the log file and determines generic stuff like\nit's ripper and language, etc.\n\"\"\"\n\nimport re\n\nfrom heybrochecklog import UnrecognizedException\nfrom heybrochecklog.resources import EAC_RIPLINES\n\n\ndef analyze_log(log):\n    \"\"\"Analyze a log file and determine some generic background information.\"\"\"\n    log.ripper = get_ripper(log.contents)\n    log.language = determine_language(log)\n\n\ndef get_ripper(contents):\n    \"\"\"Determine the ripper used in the log.\"\"\"\n    if not contents:  # Is file empty?\n        raise UnrecognizedException('Empty log file')\n\n    # Even foreign EAC logs start with this line.\n    eac_regex = re.compile(r'Exact Audio Copy V[0-1]\\.[0-9]+.*?from.*')\n    xld_regex = re.compile(r'X Lossless Decoder version ([0-9abc]+) \\([0-9\\.]+\\)')\n    if eac_regex.match(contents[0]):\n        return 'EAC'\n    elif xld_regex.match(contents[0]):\n        return 'XLD'\n    else:\n        # Unfortunately, not all EAC <=0.95 logs are English, so a compiled\n        # multi-language ripper regex pattern is necessary.\n        re_95 = re.compile('|'.join(list(EAC_RIPLINES.values())))\n        if re_95.match(contents[0]):\n            return 'EAC95'\n        else:\n            raise UnrecognizedException('Unrecognized ripper')\n\n\ndef determine_language(log):\n    \"\"\"Determine the language of the log file, and verify that it is an EAC log file.\"\"\"\n    if log.ripper == 'XLD':\n        return 'english'\n\n    useful_contents = [\n        re.sub(r'\\s+', ' ', l.rstrip()) for l in log.contents if l.strip()\n    ]\n    for line in useful_contents[:2]:\n        for language, line_starter in EAC_RIPLINES.items():\n            if re.match(line_starter, line):\n                return language\n\n    raise UnrecognizedException('Unrecognized/unsupported language')\n","heybrochecklog/logfile.py":"\"\"\"This module contains the LogFile class, an encapsulation of log variables.\"\"\"\n\nimport re\n\nfrom heybrochecklog.resources import DEDUCTIONS\n\n\nclass LogFile:\n    \"\"\"A log file class containing variables, score, deductions, etc.\"\"\"\n\n    def __init__(self, contents, ripper=None):\n\n        self.full_contents = contents\n        self.contents = format_full_contents(contents)\n        self.concat_contents = [line for line in self.contents if line.strip()]\n        self.score = 100\n        self.ripper = ripper\n        self.language = None\n        self.drive = None\n        self.version = None\n        self.album = None\n        self.unrecognized = None\n\n        # Some other log settings\n        self.range = False\n        self.cdr = False\n        self.unindexed_drive = False\n        self.htoa = False\n        self.htoa_index = False\n        self.htoa_ripped = False\n\n        # Important parts of the log\n        self.checksum = False\n        self.all_tracks = None\n        self.deductions = {}\n        self.crc_mismatch = []\n        self.track_errors = {\n            \"Aborted copy\": [],\n            \"Timing problem\": [],\n            \"Suspicious position\": [],\n            \"Missing samples\": [],\n            \"Read error\": [],\n            \"Damaged sector count\": [],\n        }\n\n        # Lists of data for the log\n        self.toc = {}\n        self.accuraterip = []\n        self.track_indices = []\n        self.tracks = {}\n\n        # Indexes of log locations\n        self.index_settings = None\n        self.index_toc = None\n        self.index_tracks = None\n        self.index_footer = None\n\n        # Flagged = auto report log\n        self.flagged = False\n\n    def to_dict(self):\n        \"\"\"Return a dict of the log analysis.\"\"\"\n        if self.unrecognized:\n            return {\n                'unrecognized': self.unrecognized,\n                'flagged': self.flagged,\n                'contents': ''.join(self.full_contents),\n            }\n\n        deductions = [deduction for deduction in self.deductions.values()]\n\n        return {\n            'deductions': deductions,\n            'flagged': self.flagged,\n            'name': self.album,\n            'ripper': self.ripper,\n            'score': self.score,\n            'version': self.version,\n            'unrecognized': False,\n            'contents': ''.join(self.full_contents),\n        }\n\n    def add_deduction(\n        self, deduction, multiplier=1, track=None, extra_phrase=None, cap_10=False\n    ):\n        \"\"\"Add a deduction to the log file.\"\"\"\n        name, score = self._get_deduction_from_dict(deduction)\n        if score:\n            score = score * multiplier if not cap_10 else score * min(10, multiplier)\n\n        if track:\n            name = 'Track {}: {}'.format(track, name)\n        if multiplier > 1:\n            name += ' ({} occurrences)'.format(multiplier)\n        if score:\n            name += ' (-{} points)'.format(score)\n        if extra_phrase:\n            name += ' ({})'.format(extra_phrase)\n\n        self.deductions[deduction] = [name, score]\n\n    def _get_deduction_from_dict(self, deduction):\n        \"\"\"Get the deduction's name and score from the deductions dict.\"\"\"\n        if deduction not in DEDUCTIONS:\n            return (deduction, None)\n\n        deduction_entry = DEDUCTIONS[deduction]\n        # Some deductions are different per-ripper and are represented\n        # with an extra embedded dictionary.\n        if isinstance(deduction_entry, dict):\n            if self.ripper in deduction_entry:\n                deduction_entry = deduction_entry[self.ripper]\n            else:\n                deduction_entry = deduction_entry['Default']\n\n        return (deduction_entry[0], deduction_entry[1])\n\n    def remove_deduction(self, deduction):\n        \"\"\"Remove a deduction from the log file.\"\"\"\n        if deduction in self.deductions:\n            del self.deductions[deduction]\n\n    def has_deduction(self, deduction):\n        \"\"\"Learn whether or not the log file has a deduction.\"\"\"\n        return deduction in self.deductions\n\n    def has_deductions(self, *deductions):\n        \"\"\"Return whether or not log has every deduction in deductions.\"\"\"\n        return all(de in self.deductions for de in deductions)\n\n\ndef format_full_contents(full_contents):\n    \"\"\"\n    Format raw contents by stripping spaces, blank lines, and filtering\n    out unicode crap.\n    \"\"\"\n    contents = [re.sub(r'\\s+', ' ', l.rstrip()) for l in full_contents]\n    contents = [re.sub('：', ':', l) for l in contents]\n    contents = [re.sub('，', ', ', l) for l in contents]\n\n    return contents\n","heybrochecklog/resources/__init__.py":"EAC_RIPLINES = {\n    'english': 'EAC extraction logfile from',\n    'slovak': 'EAC log súbor extrakcie z',\n    'bulgarian': 'Отчет на EAC за извличане, извършено на',\n    'swedish': 'EAC extraheringsloggfil från',\n    'dutch': 'EAC uitlezen log bestand van',\n    'spanish': 'Archivo Log de extracciones desde',\n    'italian': 'File di log EAC per l\\'estrazione del',\n    'german': 'EAC Auslese-Logdatei vom',\n    'serbian': 'EAC-ov fajl dnevnika ekstrakcije iz',\n    'russian': 'Отчёт EAC об извлечении, выполненном',\n    'french': 'Journal d\\'extraction EAC depuis',\n    'uzbek': 'EAC ajratish logfayli',\n    'chinese': 'EAC 抓取日志文件从',\n    'polish': 'Sprawozdanie ze zgrywania programem EAC z',\n    'czech': 'Protokol extrakce EAC z',\n}\n\nDEDUCTIONS = {\n    'Read mode': {\n        'XLD': ['Ripper mode was not XLD Secure Ripper', 100],\n        'Default': ['Read mode was not secure', 20],\n    },\n    'Accurate stream': ['Accurate stream was not used', 20],\n    'Audio cache': ['Audio cache not defeated', 10],\n    'C2 pointers': ['C2 pointers were used', 20],\n    'Drive offset': ['Incorrect read offset for drive', 5],\n    'Combined offset': ['Combined read/write offset cannot be verified', 5],\n    'Zero offset': [\n        'The drive could not be found in the database; however, an offset '\n        'of 0 is rarely correct',\n        5,\n    ],  # noqa E501\n    'Fill missing offset samples with silence': [\n        'Missing offset samples not filled up with silence',\n        5,\n    ],\n    'Deleting silent blocks': ['Deletes leading and trailing silent blocks', 5],\n    'Null samples': ['Null samples were not used in CRC calculations', 1],\n    'Gap handling': ['Gaps were not analyzed and appended', 10],\n    'ID3 tags': ['ID3 tags added to FLAC files', 1],\n    'AccurateRip': ['AccurateRip was not enabled', 5],\n    'Test & Copy': ['Test & Copy was not used', 20],\n    'Range rip': ['Range rip detected', 20],\n    'EAC 0.95': ['EAC 0.95 log or older', 30],\n    'HTOA not ripped twice': ['HTOA was not ripped twice', 10],\n    'Improper HTOA extraction': ['HTOA was improperly extracted', 10],\n    'CRC mismatch on HTOA extraction': ['CRC mismatch on HTOA extraction', 10],\n    'Aborted copy': ['Aborted copy', 100],\n    'CRC mismatch': ['CRC mismatch', 30],\n    'Timing problem': ['Timing problem', 20],\n    'Suspicious position': ['Suspicious position', 20],\n    'Missing sample': ['Missing sample', 20],\n    'Track gain': ['Track gain was not turned on', 1],\n    'Read error': ['Read error', 1],\n    'Damaged sector count': ['Damaged sector', 1],\n    'Checksum': ['No checksum', 15],\n    'Virtual drive': ['Virtual drive was used', 100],\n    'CD-R': ['CD-R detected; not a pressed CD', 0],\n    'AccurateRip discrepancies': [\n        'AccurateRip discrepancies; rip may contain silent errors',\n        None,\n    ],\n    'Normalization': ['Destructive normalization used', 100],\n    'Compression offset': ['Ripped with compression offset', 100],\n}\n\nVERSIONS = {\n    'EAC': [\n        ('V1.4', '3. February 2020'),\n        ('V1.3', '2. September 2016'),\n        ('V1.2', '12. August 2016'),\n        ('V1.1', '23. June 2015'),\n        ('V1.0 beta 6', '9. April 2015'),\n        ('V1.0 beta 5', '2. April 2015'),\n        ('V1.0 beta 4', '7. December 2014'),\n        ('V1.0 beta 3', '29. August 2011'),\n        ('V1.0 beta 2', '29. April 2011'),\n        ('V1.0 beta 1', '15. November 2010'),\n        ('V0.99 prebeta 5', '4. May 2009'),\n        ('V0.99 prebeta 4', '23. January 2008'),\n        ('V0.99 prebeta 3', '28. July 2007'),\n        ('V0.99 prebeta 2', '28 July 2007'),\n        ('V0.99 prebeta 1', '25. May 2007'),\n        ('EAC <=0.95', '1. April 1970'),\n    ],\n    'XLD': [\n        ('20191014', '152.0'),\n        ('20181019', '151.1'),\n        ('20170729', '150.3'),\n        ('20170710', '150.2'),\n        ('20161007', '149.3'),\n        ('20160920', '149.2'),\n        ('20151214', '149.1'),\n        ('20151128', '149.0'),\n        ('20141129a', '148.2'),\n        ('20141129', '148.1'),\n        ('20141109', '148.0'),\n        ('20140504', '147.0'),\n        ('20140427', '146.0'),\n        ('20131102', '145.0'),\n        ('20130720', '144.0'),\n        ('20130602', '143.2'),\n        ('20130601', '143.1'),\n        ('20130407', '143.0'),\n        ('20130127', '142.3'),\n        ('20121222', '142.2'),\n        ('20121027', '142.1'),\n        ('20121013', '142.0'),\n        ('20120908', '141.1'),\n        ('20120609', '141.0'),\n        ('20120407', '140.0'),\n        ('20120226', '139.1'),\n        ('20120120', '139.0'),\n        ('20111214', '138.1'),\n        ('20111211', '138.0'),\n        ('20111113', '137.1'),\n        ('20111113', '137.0'),\n        ('20111015', '136.4'),\n        ('20110924', '136.3'),\n        ('20110821', '136.1'),\n        ('20110820', '136.0'),\n        ('20110703', '135.1'),\n        ('20110611', '135.0'),\n        ('20110528', '134.0'),\n        ('20110515', '133.1'),\n        ('20110508', '133.0'),\n        ('20110502', '132.0'),\n        ('20110417', '131.0'),\n        ('20110312', '130.0'),\n        ('20110228', '129.0'),\n        ('20110226', '128.0'),\n        ('20110212', '127.0'),\n        ('20101212', '126.2'),\n        ('20101208', '126.1'),\n        ('20101128', '126.0'),\n        ('20101120', '125.2'),\n        ('20101117', '125.1'),\n        ('20101115', '125.0'),\n        ('20101107', '124.1'),\n        ('20101107', '124.0'),\n        ('20101031', '123.7'),\n        ('20101027', '123.6'),\n        ('20101023', '123.5'),\n        ('20101023', '123.4'),\n        ('20101010', '123.3'),\n        ('20100926', '123.1'),\n        ('20100926', '123.0'),\n        ('20100918', '122.1'),\n        ('20100911', '122.0'),\n        ('20100908', '121.2'),\n        ('20100907', '121.1'),\n        ('20100907', '121.0'),\n        ('20100904', '120.7'),\n        ('20100828', '120.6'),\n        ('20100711', '120.5'),\n        ('20100704', '120.4'),\n        ('20100518', '120.3'),\n        ('20100516', '120.2'),\n        ('20100515', '119.3'),\n        ('20100511', '119.2'),\n        ('20100505', '119.1'),\n        ('20100503', '119.0'),\n        ('20100424', '118.0'),\n        ('20100417', '117.4'),\n        ('20100412', '117.3'),\n        ('20100409', '117.2'),\n        ('20100401', '117.0'),\n        ('20100323', '116.7'),\n        ('20100302', '116.6'),\n        ('20100301', '116.5'),\n        ('20100218', '116.4'),\n        ('20100217', '116.3'),\n        ('20100214', '116.2'),\n        ('20100209', '116.0'),\n        ('20100206', '115.6'),\n        ('20100205', '115.5'),\n        ('20100123', '115.4'),\n        ('20100117', '115.3'),\n        ('20091230', '115.2'),\n        ('20091225', '115.1'),\n        ('20091223', '115.0'),\n        ('20091212', '114.6'),\n        ('20091202', '114.5'),\n        ('20091202', '114.4'),\n        ('20091129', '114.3'),\n        ('20091127', '114.2'),\n        ('20091125', '114.1'),\n        ('20091123', '114.0'),\n        ('20091121', '113.4'),\n        ('20091115', '113.3'),\n        ('20091114', '113.2'),\n        ('20091112', '113.1'),\n        ('20091111', '113.0'),\n        ('20091108', '112.0'),\n        ('20091107', '111.0'),\n        ('20090930', '110.0'),\n        ('20090924', '109.9'),\n        ('20090922', '109.8'),\n        ('20090910', '109.7'),\n        ('20090829', '109.6'),\n        ('20090828', '109.5'),\n        ('20090827a', '109.4'),\n        ('20090824a', '109.2'),\n        ('20090824', '109.1'),\n        ('20090320', '105.1'),\n        ('20090318', '105.0'),\n        ('20090317', '104.4'),\n        ('20090315a', '104.3'),\n        ('20090315', '104.2'),\n        ('20090314', '104.1'),\n        ('20090217', '101.1'),\n        ('20090215', '100.1'),\n        ('20080926', '93.3'),\n        ('20080925', '93.2'),\n        ('20080921', '92.1'),\n        ('20080916c', '91.3'),\n        ('20080914a', '90.1'),\n        ('86.1', '20080907a'),\n    ],\n}\n","heybrochecklog/resources/eac/english.json":"{\n    \"patterns\": {\n        \"drive\": [\"Used [Dd]rive\"],\n        \"settings\": {\n            \"Read mode\": [\"Read mode\"],\n            \"Accurate stream\": [\"Utilize accurate stream\"],\n            \"Audio cache\": [\"Defeat audio cache\"],\n            \"C2 pointers\": [\"Make use of C2 pointers\"],\n            \"Drive offset\": [\"Read offset correction\"],\n            \"Fill missing offset samples with silence\": [\"Fill up missing offset samples with silence\"],\n            \"Deleting silent blocks\": [\"Delete leading and trailing silent blocks\"],\n            \"Null samples\": [\"Null samples used in CRC calculations\"],\n            \"Gap handling\": [\"Gap handling\"],\n            \"ID3 tags\": [\"Add ID3 tag\"]\n        },\n        \"bad settings\": {\n            \"Normalization\": [\"Normalize to\"],\n            \"Compression offset\": [\"Use compression offset\"],\n            \"Combined offset\": [\"Combined read/write offset correction\"]\n        },\n        \"proper settings\": {\n            \"Read mode\": [\"Secure\"],\n            \"Accurate stream\": [\"Yes\"],\n            \"Audio cache\": [\"Yes\"],\n            \"C2 pointers\": [\"No\"],\n            \"Fill missing offset samples with silence\": [\"Yes\"],\n            \"Deleting silent blocks\": [\"No\"],\n            \"Null samples\": [\"Yes\"],\n            \"Gap handling\": [\"Appended to previous track\"],\n            \"ID3 tags\": [\"No\"]\n        },\n        \"toc\": [\"TOC of the extracted CD\"],\n        \"range\": [\"Range status and errors\"],\n        \"htoa\": [\"Selected range \\\\(Sectors 0-([0-9]+)\\\\)\"],\n        \"track\": [\"Track\"],\n        \"track settings\": {\n            \"filename\": [\"Filename\"],\n            \"pregap\": [\"Pre-gap length\"],\n            \"peak\": [\"Peak level\"],\n            \"test crc\": [\"Test CRC\"],\n            \"copy crc\": [\"(?:Copy CRC|CRC)\"]\n        },\n        \"track errors\": {\n            \"Aborted copy\": [\"Copy aborted\"],\n            \"Timing problem\": [\"Timing problem\"],\n            \"Missing samples\": [\"Missing samples\"],\n            \"Suspicious position\": [\"Suspicious position\"]\n        },\n        \"accuraterip\": {\n            \"match\": [\"Accurately ripped \\\\(confidence ([0-9]+)\\\\) \\\\[[A-Z0-9]{8}\\\\]\"],\n            \"no match\": [\"Cannot be verified as accurate \\\\(confidence ([0-9]+)\\\\) \\\\[[A-Z0-9]{8}\\\\]\"],\n            \"bad match\": [\"Not accurately ripped \\\\(confidence ([0-9]+)\\\\) \\\\[[A-Z0-9]{8}\\\\]\"],\n            \"no result\": [\"Track not present in AccurateRip database\"],\n            \"not ripped\": [\"Track not fully ripped for AccurateRip lookup\"]\n        },\n        \"range accuraterip\": {\n            \"match\": [\"Track [0-9]+ accurately ripped \\\\(confidence ([0-9]+)\\\\) \\\\[[A-Z0-9]{8}]\"],\n            \"no match\": [\"Track [0-9]+ not ripped accurately \\\\(confidence ([0-9]+)\\\\) \\\\[[A-Z0-9]{8}]\"],\n            \"no result\": [\"Track [0-9]+ not present in database\"]\n        },\n        \"footer\": [\"End of status report\"],\n        \"checksum\": [\"==== Log checksum [A-Z0-9]{64} ====\"]\n    },\n    \"translation\": {\n        \"1\": [\"English\"],\n        \"2\": [\"English\"],\n        \"5\": [\"Error Message\"],\n        \"6\": [\"Warning\"],\n        \"7\": [\"Success\"],\n        \"8\": [\"Information\"],\n        \"10\": [\"OK\"],\n        \"11\": [\"Cancel\"],\n        \"12\": [\"Apply\"],\n        \"15\": [\"Yes\"],\n        \"16\": [\"No\"],\n        \"31\": [\"Filename will be ignored\"],\n        \"50\": [\"Value out of range !\"],\n        \"51\": [\"Invalid characters !\"],\n        \"52\": [\"Invalid filename !\"],\n        \"4270\": [\"Low\"],\n        \"4271\": [\"Medium\"],\n        \"4272\": [\"High\"],\n        \"1200\": [\"Status and Error Messages\"],\n        \"2501\": [\"Track status and errors\"],\n        \"1203\": [\"Possible Errors\"],\n        \"1204\": [\"Create Log\"],\n        \"1210\": [\"Range status and errors\"],\n        \"1211\": [\"Selected range\"],\n        \"1212\": [\"Timing problem\"],\n        \"1213\": [\"Suspicious position\"],\n        \"1214\": [\"Missing samples\"],\n        \"1215\": [\"Too many samples\"],\n        \"1216\": [\"File write error\"],\n        \"1217\": [\"Peak level\"],\n        \"1299\": [\"Extraction speed\"],\n        \"1218\": [\"Range quality\"],\n        \"1219\": [\"CRC\"],\n        \"1220\": [\"Copy OK\"],\n        \"1221\": [\"Copy finished\"],\n        \"1227\": [\"Track quality\"],\n        \"1228\": [\"Copy aborted\"],\n        \"1269\": [\"Filename\"],\n        \"1270\": [\"Pre-gap length\"],\n        \"1271\": [\"Test CRC\"],\n        \"1272\": [\"Copy CRC\"],\n        \"1273\": [\"Compressing\"],\n        \"1280\": [\"Track not fully ripped for AccurateRip lookup\"],\n        \"1281\": [\"Accurately ripped (confidence\"],\n        \"1282\": [\"Not accurately ripped (confidence\"],\n        \"1283\": [\"Track not present in AccurateRip database\"],\n        \"1330\": [\"Cannot be verified as accurate\"],\n        \"1337\": [\"cannot be verified as accurate\"],\n        \"1331\": [\", AccurateRip returned\"],\n        \"1332\": [\"(confidence\"],\n        \"1333\": [\"No tracks could be verified as accurate\"],\n        \"1334\": [\"You may have a different pressing from the one(s) in the database\"],\n        \"1335\": [\"Some tracks could not be verified as accurate\"],\n        \"1336\": [\"All tracks accurately ripped\"],\n        \"1338\": [\"Null samples used in CRC calculations\"],\n        \"1339\": [\"track(s) not present in the AccurateRip database\"],\n        \"1340\": [\"track(s) accurately ripped\"],\n        \"1341\": [\"track(s) could not be verified as accurate\"],\n        \"1342\": [\"track(s) not fully ripped for AccurateRip lookup\"],\n        \"1343\": [\"track(s) canceled\"],\n        \"1344\": [\"None of the tracks are present in the AccurateRip database\"],\n        \"1284\": [\"Not all tracks ripped accurately\"],\n        \"1222\": [\"No errors occurred\"],\n        \"1223\": [\"Review Range\"],\n        \"1224\": [\"There were errors\"],\n        \"1225\": [\"End of status report\"],\n        \"1325\": [\"Log checksum\"],\n        \"1226\": [\"Track\"],\n        \"1230\": [\"Index\"],\n        \"1229\": [\"Review Tracks\"],\n        \"1274\": [\"EAC extraction logfile from\"],\n        \"1240\": [\"January\"],\n        \"1241\": [\"February\"],\n        \"1242\": [\"March\"],\n        \"1243\": [\"April\"],\n        \"1244\": [\"May\"],\n        \"1245\": [\"June\"],\n        \"1246\": [\"July\"],\n        \"1247\": [\"August\"],\n        \"1248\": [\"September\"],\n        \"1249\": [\"October\"],\n        \"1250\": [\"November\"],\n        \"1251\": [\"December\"],\n        \"1232\": [\"EAC extraction log file\"],\n        \"1233\": [\"Used drive  :\"],\n        \"1234\": [\"Read mode\"],\n        \"1235\": [\"Burst\"],\n        \"1236\": [\"Fast\"],\n        \"1237\": [\"Paranoid\"],\n        \"1238\": [\"Secure with NO C2, accurate stream, NO disable cache\"],\n        \"1239\": [\"Secure with NO C2, NO accurate stream, disable cache\"],\n        \"1252\": [\"Secure with C2, accurate stream, NO disable cache\"],\n        \"1253\": [\"Secure with C2, accurate stream, disable cache\"],\n        \"1254\": [\"Secure with NO C2, accurate stream, disable cache\"],\n        \"1255\": [\"Combined read/write offset correction\"],\n        \"1256\": [\"Read offset correction\"],\n        \"1257\": [\"Overread into Lead-In and Lead-Out\"],\n        \"1258\": [\"Used output format\"],\n        \"1259\": [\"Additional command line options\"],\n        \"1260\": [\"Internal WAV Routines\"],\n        \"1261\": [\"44.100 Hz; 16 Bit; Stereo\"],\n        \"1262\": [\"Use compression offset\"],\n        \"1263\": [\"Other options      :\"],\n        \"1264\": [\"Fill up missing offset samples with silence\"],\n        \"1265\": [\"Delete leading and trailing silent blocks\"],\n        \"1266\": [\"Normalize to\"],\n        \"1267\": [\"Native Win32 interface for Win NT & 2000\"],\n        \"1268\": [\"Installed external ASPI interface\"],\n        \"1275\": [\"AccurateRip summary\"],\n        \"1276\": [\"not ripped completely\"],\n        \"1277\": [\"accurately ripped (confidence\"],\n        \"1278\": [\"not ripped accurately (confidence\"],\n        \"1279\": [\"not present in database\"],\n        \"1285\": [\", but should be\"],\n        \"1286\": [\"Performing a test extraction only\"],\n        \"1287\": [\"Sectors\"],\n        \"1288\": [\"Exact Audio Copy\"],\n        \"1289\": [\"TOC of the extracted CD\"],\n        \"1290\": [\"Track\"],\n        \"1291\": [\"Start\"],\n        \"1292\": [\"Length\"],\n        \"1293\": [\"Start sector\"],\n        \"1294\": [\"End sector\"],\n        \"1295\": [\"Secure\"],\n        \"1296\": [\"Make use of C2 pointers\"],\n        \"1297\": [\"Utilize accurate stream\"],\n        \"1298\": [\"Defeat audio cache\"],\n        \"1305\": [\"Used interface\"],\n        \"1306\": [\"Command line compressor\"],\n        \"1307\": [\"Selected bitrate\"],\n        \"1308\": [\"Quality\"],\n        \"1328\": [\"High\"],\n        \"1329\": [\"Low\"],\n        \"1309\": [\"Add ID3 tag\"],\n        \"1310\": [\"Sample format\"],\n        \"1320\": [\"Gap handling\"],\n        \"1321\": [\"Not detected, thus appended to previous track\"],\n        \"1322\": [\"Appended to previous track\"],\n        \"1323\": [\"Appended to next track\"],\n        \"1324\": [\"Left out\"],\n        \"81700\": [\"L3Enc MP3 Encoder & Compatible\"],\n        \"81701\": [\"Fraunhofer MP3Enc MP3 Encoder\"],\n        \"81702\": [\"Xing X3Enc MP3 Encoder\"],\n        \"81703\": [\"Xing ToMPG MP3 Encoder\"],\n        \"81704\": [\"LAME MP3 Encoder\"],\n        \"81705\": [\"GOGO MP3 Encoder\"],\n        \"81706\": [\"MPC Encoder\"],\n        \"81707\": [\"Ogg Vorbis Encoder\"],\n        \"81708\": [\"Microsoft WMA9 Encoder\"],\n        \"81709\": [\"FAAC AAC Encoder\"],\n        \"81710\": [\"Homeboy AAC Encoder\"],\n        \"81711\": [\"Quartex AAC Encoder\"],\n        \"81712\": [\"PsyTEL AAC Encoder\"],\n        \"81713\": [\"MBSoft AAC Encoder\"],\n        \"81714\": [\"Yamaha VQF Encoder\"],\n        \"81715\": [\"Real Audio Encoder\"],\n        \"81716\": [\"Monkey's Audio Lossless Encoder\"],\n        \"81717\": [\"Shorten Lossless Encoder\"],\n        \"81718\": [\"RKAU Lossless Encoder\"],\n        \"81719\": [\"LPAC Lossless Encoder\"],\n        \"81720\": [\"User Defined Encoder\"]\n    }\n}","heybrochecklog/resources/xld.json":"{\n    \"drive\": [\"Used [Dd]rive\"],\n    \"settings\": {\n        \"Read mode\": [\"(?:Ripper|Use cdparanoia) mode\"],\n        \"Audio cache\": [\"Disable audio cache\"],\n        \"C2 pointers\": [\"Make use of C2 pointers\"],\n        \"Drive offset\": [\"Read offset correction\"],\n        \"Gap handling\": [\"Gap status\"]\n    },\n    \"proper settings\": {\n        \"Read mode\": [\"XLD Secure Ripper\"],\n        \"Audio cache\": [\"OK for the drive with(?: a)? cache less than [0-9]+Ki?B\"],\n        \"C2 pointers\": [\"NO\"],\n        \"Gap handling\": [\"Analyzed, Appended(?: \\\\(except HTOA\\\\))?\"]\n    },\n    \"disc type\": [\"Media type\"],\n    \"toc\": [\"TOC of the extracted CD\"],\n    \"track\": [\"Track\"],\n    \"All Tracks\": [\"All Tracks\"],\n    \"track settings\": {\n        \"gain\": [\"Track gain\"],\n        \"filename\": [\"Filename\"],\n        \"pregap\": [\"Pre-gap length\"],\n        \"peak\": [\"Peak\"],\n        \"test crc\": [\"CRC32 hash \\\\(test run\\\\)\"],\n        \"copy crc\": [\"CRC32 hash\"]\n    },\n    \"track errors\": {\n        \"Read error\": [\"Read error\"],\n        \"Damaged sector count\": [\"Damaged sector count\"]\n    },\n    \"accuraterip\": {\n        \"match\": [\"->Accurately ripped!? \\\\((?:[v12,+]+ )?confidence ([0-9\\\\/+]+)\\\\)\"],\n        \"no match\": [\"->Track not present in AccurateRip database\"]\n    },\n    \"footer\": [\"End of status report\"],\n    \"checksum\": [\"-----BEGIN XLD SIGNATURE-----\"]\n}\n","heybrochecklog/score/__init__.py":"\"\"\"This module handles the log scoring functionality of the heybrochecklog package.\"\"\"\n\nimport html\n\nfrom heybrochecklog import UnrecognizedException\nfrom heybrochecklog.analyze import analyze_log\nfrom heybrochecklog.logfile import LogFile\nfrom heybrochecklog.score import eac, eac95, xld\nfrom heybrochecklog.shared import get_log_contents, open_json\n\n\ndef score_log(log_file, markup=False):\n    try:\n        contents = get_log_contents(log_file)\n        log = LogFile(contents)\n        log = score_wrapper(log, markup)\n    except UnicodeDecodeError:\n        log = LogFile('')\n        log.unrecognized = 'Could not decode log file.'\n    return log.to_dict()\n\n\ndef score_log_from_contents(contents):\n    \"\"\"Score a log file given its contents, instead of opening it from a file.\"\"\"\n    log = LogFile(contents.split('\\n'))\n    try:\n        log = score_wrapper(log)\n    except UnicodeDecodeError:\n        log.unrecognized = 'Could not decode log file.'\n    return log.to_dict()\n\n\ndef score_wrapper(log, markup=False):\n    \"\"\"Determine the type of log file and passes the log to the appropriate logchecker.\"\"\"\n\n    try:\n        analyze_log(log)\n    except UnrecognizedException as exception:\n        log.unrecognized = str(exception)\n        log.full_contents = [html.escape(line) for line in log.full_contents]\n        return log\n\n    if log.ripper == 'EAC':\n        info_json = open_json('eac', '{}.json'.format(log.language))\n        logchecker = eac.EACChecker(\n            info_json['patterns'], info_json['translation'], markup\n        )\n    elif log.ripper == 'XLD':\n        patterns = open_json('xld.json')\n        logchecker = xld.XLDChecker(patterns, markup=markup)\n    elif log.ripper == 'EAC95':\n        info_json = open_json('eac95', '{}.json'.format(log.language))\n        logchecker = eac95.EAC95Checker(\n            info_json['patterns'], info_json['translation'], markup\n        )\n\n    try:\n        log = logchecker.check(log)\n    except UnrecognizedException as exception:\n        log.unrecognized = str(exception)\n        log.full_contents = [html.escape(line) for line in log.full_contents]\n\n    return log\n","heybrochecklog/score/eac.py":"\"\"\"This module contains the EAC Log Checker.\"\"\"\n\nimport re\n\nfrom heybrochecklog import UnrecognizedException\ndef markup(*args, **kwargs):\n    return None\nfrom heybrochecklog.score.logchecker import LogChecker\nfrom heybrochecklog.score.modules import combined, parsers, validation\nfrom heybrochecklog.shared import format_pattern as fmt_ptn\n\n\nclass EACChecker(LogChecker):\n    \"\"\"This class analyzes >0.95 EAC Log Files.\"\"\"\n\n    def check(self, main_log):\n        \"\"\"Checks the EAC logs.\"\"\"\n        logs = combined.split_combined(main_log)\n        for log in logs:\n            if len(log.concat_contents) < 20:\n                raise UnrecognizedException('Cannot parse log file; log file too short')\n\n            log.version = self.check_version(log)\n            log.album = log.concat_contents[2]\n            log.drive = self.check_drive(log)\n\n            self.index_log(log)\n            self.evaluate_settings(log)\n            parsers.index_toc(log)\n            self.is_there_a_htoa(log)\n            self.check_tracks(log)\n            parsers.parse_checksum(\n                log, self.patterns['checksum'], 'V1.0 beta 1', 'EAC <1.0'\n            )\n            if self.markup:\n                markup(log, self.patterns, self.translation)\n\n        main_log = combined.defragment(logs)\n        validation.validate_track_count(main_log)\n        validation.validate_track_settings(main_log)\n        self.deduct_and_score(main_log)\n\n        return main_log\n\n    def check_version(self, log):\n        \"\"\"Check the version of the log and verify it is acceptable.\"\"\"\n        regex = re.compile(r'Exact Audio Copy (V.*) from (.*)')\n        return self.verify_version(regex, log.concat_contents[0], 'EAC')\n\n    def check_drive(self, log):\n        \"\"\"Check the drive of the log and verify it is an allowed drive.\"\"\"\n        regex = r' ?: (.*) Adapter:[ 0-9]+ID:[ 0-9]+$'\n        return self.get_drive(regex, log.concat_contents[3])\n\n    def all_range_index(self, log, line):\n        \"\"\"Match the Range Rip line in the log file.\"\"\"\n        if log.index_tracks is None and re.match(fmt_ptn(self.patterns['range']), line):\n            return True\n        return False\n\n    def all_range_index_action(self, log, line_num):\n        \"\"\"Action to take when the range rip line is matched.\"\"\"\n        log.track_indices.append(line_num)\n        log.range = True\n\n    def check_bad_settings(self, log, line):\n        \"\"\"Evaluate the instant -100 point deductions.\"\"\"\n        bad_settings = self.patterns['bad settings']\n        for sett, pattern in bad_settings.items():\n            if re.match(fmt_ptn(pattern), line):\n                log.add_deduction(sett)\n\n    def evaluate_unmatched_settings(self, log, settings):\n        \"\"\"Override super to account for burst mode not having some settings.\"\"\"\n        burst_no_exist = ['Accurate stream', 'Audio cache', 'C2 pointers']\n        if log.has_deduction('Read mode'):\n            if any(setting not in settings for setting in burst_no_exist):\n                raise UnrecognizedException(\n                    'Invalid rip settings for a burst/fast mode rip'\n                )\n            for setting in burst_no_exist:\n                del settings[setting]\n\n        if log.has_deduction('Combined offset') and 'Drive offset' in settings:\n            del settings['Drive offset']\n\n        super().evaluate_unmatched_settings(log, settings)\n\n    def is_there_a_htoa(self, log):\n        \"\"\"Check rip for Hidden Track One Audio.\"\"\"\n        # 6 second minimum for HTOA per EAC standards\n        # Only accepted HTOA extraction technique for EAC is range-based\n        if log.toc[1][0] < 450 or not log.range:\n            return\n\n        for line in log.contents[log.index_tracks + 1 :]:\n            if line.strip():\n                result = re.search(fmt_ptn(self.patterns['htoa']), line)\n                if result:\n                    log.htoa = True\n                    log.htoa_index = log.toc[1][0] - 1\n\n                    # Remove the gap handling notification (doesn't appear for\n                    # range rips)\n                    if log.has_deduction('Could not verify gap handling'):\n                        log.remove_deduction('Could not verify gap handling')\n\n                    if int(result.group(1)) == log.htoa_index:\n                        log.htoa_ripped = True\n                        log.add_deduction('HTOA extracted')\n                    else:\n                        log.add_deduction('Improper HTOA extraction')\n                else:\n                    log.add_deduction('HTOA detected, but not extracted')\n                break\n\n    def check_tracks(self, log):\n        \"\"\"Wrapper for the analyze_tracks method. Get track data for every track\n        and check for errors.\n        \"\"\"\n        tsettings = self.patterns['track settings']\n        track_settings = {\n            'filename': re.compile(r'\\s+' + fmt_ptn(tsettings['filename']) + r' (.*)'),\n            'pregap': re.compile(\n                r'\\s+' + fmt_ptn(tsettings['pregap']) + r' ([0-9:\\.]+)'\n            ),\n            'peak': re.compile(r'\\s+' + fmt_ptn(tsettings['peak']) + r' ([0-9\\.]+) %'),\n            'test crc': re.compile(\n                r'\\s+' + fmt_ptn(tsettings['test crc']) + r' ([A-Z0-9]{8})'\n            ),\n            'copy crc': re.compile(\n                r'\\s+' + fmt_ptn(tsettings['copy crc']) + r' ([A-Z0-9]{8})'\n            ),\n        }\n\n        self.analyze_tracks(log, track_settings, parsers.parse_errors_eac)\n\n    def evaluate_tracks(self, log):\n        \"\"\"Evaluate the analyzed track data for deficiencies.\"\"\"\n        # AccurateRip for EAC Range Rip - AR results are at the bottom of the log.\n        if log.range:\n            patterns = self.patterns['range accuraterip'].items()\n            parsers.parse_range_accuraterip(log, patterns)\n\n        # HTOA doesn't need these deductions, since it's supposed to be range ripping.\n        if not log.htoa:\n            if log.range:\n                log.add_deduction('Range rip')\n            # Check AccurateRip - Mismatching AR results can indicate problems even with T&C\n            validation.analyze_accuraterip(log)\n\n    def deduct_and_score(self, log):\n        \"\"\"Process the accumulated deductions and score the log file.\"\"\"\n        # Deduct for all the per-track accumulated deductions.\n        for error in log.track_errors:\n            if log.track_errors[error]:\n                log.add_deduction(error, len(log.track_errors[error]))\n\n        super().deduct_and_score(log)\n","heybrochecklog/score/xld.py":"\"\"\"This module contains the XLD Log Checker.\"\"\"\n\nimport re\n\nfrom heybrochecklog import UnrecognizedException\ndef markup(*args, **kwargs):\n    return None\nfrom heybrochecklog.score.logchecker import LogChecker\nfrom heybrochecklog.score.modules import parsers, validation\nfrom heybrochecklog.shared import format_pattern as fmt_ptn\n\n\nclass XLDChecker(LogChecker):\n    \"\"\"This class analyzes XLD Log Files.\"\"\"\n\n    def check(self, log):\n        \"\"\"Checks the XLD logs.\"\"\"\n        if len(log.contents) < 25:\n            raise UnrecognizedException('Cannot parse log file; log file too short')\n\n        log.version = self.check_version(log)\n        log.album = log.concat_contents[2]\n        log.drive = self.check_drive(log)\n        self.check_cdr(log)\n\n        self.index_log(log)\n        self.evaluate_settings(log)\n        parsers.index_toc(log)\n        self.check_tracks(log)\n        self.is_there_a_htoa(log)\n        validation.validate_track_count(log)\n        validation.validate_track_settings(log, xld=True)\n        parsers.parse_checksum(\n            log, self.patterns['checksum'], '20121222', 'XLD pre-142.2'\n        )\n\n        self.deduct_and_score(log)\n        if self.markup:\n            markup(log, self.patterns, self.translation)\n\n        return log\n\n    def check_version(self, log):\n        \"\"\"Check the version of the log and verify it is acceptable.\"\"\"\n        regex = re.compile(r'X Lossless Decoder version ([0-9abc]+) \\(([0-9\\.]+)\\)')\n        return self.verify_version(regex, log.concat_contents[0], 'XLD')\n\n    def check_drive(self, log):\n        \"\"\"Check the drive of the log and verify it is an allowed drive.\"\"\"\n        regex = r' *: (.*)?(?: +\\(revision [A-Z0-9\\.]\\))?$'\n        return self.get_drive(regex, log.concat_contents[3])\n\n    def check_cdr(self, log):\n        \"\"\"Check the log to see if CD-R is flagged.\"\"\"\n        result = re.search(\n            fmt_ptn(self.patterns['disc type']) + r' : (.*)', log.concat_contents[4],\n        )\n        if result:\n            if result.group(1) == 'Pressed CD':\n                return\n            elif result.group(1) == 'CD-Recordable':\n                log.cdr = True\n                log.flagged = True\n                log.add_deduction('CD-R')\n            else:\n                raise UnrecognizedException('Unknown disc type')\n\n    def all_range_index(self, log, line):\n        \"\"\"Match the Range Rip line in the log file.\"\"\"\n        if log.all_tracks is None and re.match(\n            fmt_ptn(self.patterns['All Tracks']), line\n        ):\n            return True\n        return False\n\n    def all_range_index_action(self, log, line_num):\n        \"\"\"Action to take when the range rip line is matched.\"\"\"\n        log.all_tracks = line_num\n\n    def is_there_a_htoa(self, log):\n        \"\"\"Check rip for Hidden Track One Audio.\"\"\"\n        # 450 sectors or 6 seconds minimum, one track rip with pregap (containing HTOA)\n        # appended to the first track. It is then split from the first track with\n        # Audacity/Audition.\n        if len(log.tracks) == 1 and log.toc[1][0] >= 450:\n            for line in log.contents[log.index_settings : log.index_toc]:\n                if re.match(r'Gap status +: Analyzed, Appended$', line):\n                    log.add_deduction('HTOA extracted')\n                    break\n\n    def check_tracks(self, log):\n        \"\"\"Get track data for each track and check for errors.\"\"\"\n        tsettings = self.patterns['track settings']\n        track_settings = {\n            'filename': re.compile(\n                r'\\s+' + fmt_ptn(tsettings['filename']) + r' : (.*?\\/.*?\\..*)'\n            ),\n            'pregap': re.compile(\n                r'\\s+' + fmt_ptn(tsettings['pregap']) + r' : ([0-9:\\.]+)'\n            ),\n            'gain': re.compile(\n                r'\\s+' + fmt_ptn(tsettings['gain']) + r' : ([A-Za-z0-9\\.-]+)'\n            ),\n            'peak': re.compile(r'\\s+' + fmt_ptn(tsettings['peak']) + r' : ([0-9\\.]+)'),\n            'test crc': re.compile(\n                r'\\s+' + fmt_ptn(tsettings['test crc']) + r' : ([A-Z0-9]{8})'\n            ),\n            'copy crc': re.compile(\n                r'\\s+' + fmt_ptn(tsettings['copy crc']) + r' : ([A-Z0-9]{8})'\n            ),\n        }\n\n        if log.all_tracks:\n            for i in range(log.all_tracks, min(log.track_indices)):\n                if re.match(track_settings['filename'], log.contents[i]):\n                    log.range = True\n                    break\n\n        self.analyze_tracks(log, track_settings, parsers.parse_errors_xld)\n\n    def evaluate_tracks(self, log):\n        \"\"\"Evaluate the analyzed track data for deficiencies (actually split off the\n        logchecker-specific) stuff ;)\n        \"\"\"\n        # Deduct for a Range Rip\n        if log.range:\n            log.add_deduction('Range rip')\n\n        # Check AccurateRip - Mismatching AR results can indicate problems even with T&C\n        validation.analyze_accuraterip(log)\n\n    def deduct_and_score(self, log):\n        \"\"\"Process the accumulated deductions and score the log file.\"\"\"\n        # Check for presence of Track gain in the tracks.\n        if not all('gain' in log.tracks[track] for track in log.tracks):\n            log.add_deduction('Track gain')\n\n        # Deduct for accumulated per-track XLD deductions; since XLD\n        # accumulated deductions are deducted by an occurrence basis, we are\n        # splitting deductions into one deduction per track, capped at 10%.\n        for error in log.track_errors:\n            for each in log.track_errors[error]:\n                log.add_deduction(error, multiplier=each[1], track=each[0], cap_10=True)\n\n        super().deduct_and_score(log)\n","heybrochecklog/score/logchecker.py":"\"\"\"A module which contains the base log checker, with the code shared between\nmore specific log checkers.\n\"\"\"\n\nimport re\n\nfrom heybrochecklog import UnrecognizedException\nfrom heybrochecklog.resources import VERSIONS\nfrom heybrochecklog.score.modules import drives, parsers, validation\nfrom heybrochecklog.shared import format_pattern as fmt_ptn\n\n\nclass LogChecker:\n    \"\"\"The base log checker to be subclassed by more specific log checkers.\"\"\"\n\n    def __init__(self, patterns, translation=None, markup=False):\n        self.patterns = patterns\n        self.translation = translation\n        self.markup = markup\n\n    def verify_version(self, regex, line, ripper):\n        \"\"\"Verify the ripper version while allowing newer legitimate releases.\"\"\"\n        result = regex.search(line)\n        if result:\n            version, date = result.group(1), result.group(2)\n            if (version, date) in VERSIONS[ripper]:\n                return version\n\n            # Upstream hey-bro-check-log 1.3.2 stopped updating its hard-coded\n            # version table after EAC 1.4 / XLD 2019. Newer EAC/XLD logs keep\n            # the same log format, so accept syntactically valid newer builds\n            # and register them for later checksum/version comparisons.\n            newer_eac = (\n                ripper == 'EAC'\n                and re.fullmatch(r'V1\\.[0-9]+', version)\n            )\n            newer_xld = (\n                ripper == 'XLD'\n                and re.fullmatch(r'20[0-9]{6}[a-z]?', version)\n                and re.fullmatch(r'[0-9]+(?:\\.[0-9]+)*', date)\n            )\n            if newer_eac or newer_xld:\n                VERSIONS[ripper].insert(0, (version, date))\n                return version\n\n        raise UnrecognizedException('Unrecognized {} version'.format(ripper))\n\n    def get_drive(self, regex, line):\n        \"\"\"Get the name of the ripping drive used.\"\"\"\n        re_drive = re.compile(fmt_ptn(self.patterns['drive']) + regex)\n        result = re_drive.match(line)\n        if result:\n            return result.group(1).strip()\n        raise UnrecognizedException('Could not parse ripping drive')\n\n    def index_log(self, log, ninety_five=False):\n        \"\"\"Index key line numbers inside the log.\"\"\"\n        if ninety_five:\n            read_mode = re.compile(re.sub(' +', ' ', fmt_ptn(self.translation['1234'])))\n        else:\n            read_mode = re.compile(fmt_ptn(self.patterns['settings']['Read mode']))\n        toc = (\n            re.compile(fmt_ptn(self.patterns['toc']))\n            if 'toc' in self.patterns\n            else None\n        )\n\n        for i, line in enumerate(log.contents):\n            if log.index_settings is None and read_mode.match(line):\n                log.index_settings = i\n            elif log.index_toc is None and 'toc' in self.patterns and toc.match(line):\n                log.index_toc = i\n            elif self.all_range_index(log, line):\n                self.all_range_index_action(log, i)\n            elif re.match(fmt_ptn(self.patterns['track']) + r' [0-9]+$', line):\n                log.track_indices.append(i)\n\n        self.validate_indices(log)\n\n    def validate_indices(self, log):\n        \"\"\"Validate the indices of notable lines in the log.\"\"\"\n        if not log.track_indices:\n            raise UnrecognizedException('No tracks found')\n\n        # +4 to compensate for range rip lines\n        for i in range(max(log.track_indices) + 4, len(log.contents)):\n            # Need to check the full_contents and add the extra space for AR summary leading space.\n            if re.match(r' ?\\w', log.full_contents[i]):\n                log.track_indices.append(i - 1)\n                log.index_footer = i\n                break\n\n        log.index_tracks = min(log.track_indices)\n        if not log.index_toc:\n            log.index_toc = log.index_tracks\n\n    def all_range_index(self, log, line):\n        \"\"\"Match the All Tracks or Range Rip line, depending on subclassed ripper.\"\"\"\n        pass\n\n    def all_range_index_action(self, log, line_num):\n        \"\"\"Action to take when detection of the All Tracks or Range Rip line occurs.\"\"\"\n        pass\n\n    def evaluate_settings(self, log):\n        \"\"\"Evaluate the log for usage of proper rip settings.\"\"\"\n        psettings = self.patterns['settings']\n        proper_settings = self.patterns['proper settings']\n\n        # Compile regex beforehand\n        settings = {}\n        colon = r' : (.*)' if log.language == 'english' else r'(?: :)? : (.*)'\n        for key, setting in psettings.items():\n            settings[key] = re.compile(fmt_ptn(setting) + colon)\n\n        # Iterate through line in the settings, and verify each setting in `sets` dict\n        for line in log.contents[log.index_settings : log.index_toc]:\n            for key, setting in list(settings.items()):\n                result = setting.search(line)\n                if result and key == 'Drive offset':\n                    drives.eval_offset(log, result.group(1))\n                    del settings[key]\n                elif result:\n                    if not re.search(fmt_ptn(proper_settings[key]), result.group(1)):\n                        log.add_deduction(key)\n                    del settings[key]\n                    break\n\n            self.check_bad_settings(log, line)\n\n        self.evaluate_unmatched_settings(log, settings)\n\n    def check_bad_settings(self, log, line):\n        \"\"\"Evaluate the bad settings, override in subclass if desired.\"\"\"\n        pass\n\n    def evaluate_unmatched_settings(self, log, settings):\n        \"\"\"Evaluate all unmatched settings and deduct for them.\"\"\"\n        for key in settings:\n            if log.range and key == 'Gap handling':\n                log.add_deduction('Could not verify gap handling')\n            elif key == 'Gap handling':\n                log.add_deduction('Gap handling')\n            elif key == 'ID3 tags':\n                log.add_deduction('Could not verify presence of ID3 tags')\n            elif key == 'Null samples':\n                log.add_deduction('Could not verify usage of null samples')\n            else:\n                raise UnrecognizedException(\n                    'One or more required settings could not be found'\n                )\n\n    def analyze_tracks(self, log, track_settings, parse_errors, accuraterip=True):\n        \"\"\"Get track data for each track and check for errors.\"\"\"\n        ar_patterns = self.patterns['accuraterip'].items() if accuraterip else {}\n        err_patterns = self.patterns['track errors'].items()\n\n        for i, index in enumerate(log.track_indices):\n            track_data = {}\n            track_num = parsers.get_track_number(log, index, self.patterns['track'])\n\n            for line in log.contents[log.track_indices[i] : log.track_indices[i + 1]]:\n                # Collect the track data using the track_settings list.\n                parsers.parse_settings(track_data, track_settings, line)\n                # Log the AccurateRip results - add AR status to log.accuraterip list.\n                if accuraterip:\n                    parsers.parse_accuraterip(log, ar_patterns, line)\n                # Ripping Errors - Loop through errors in json track errors.\n                parse_errors(log, err_patterns, track_num, line)\n\n            validation.check_crc_mismatch(log, track_num, track_data)\n\n            log.tracks[track_num] = track_data\n            if log.track_indices[i + 1] == max(log.track_indices):\n                break\n\n        self.evaluate_tracks(log)\n\n    def evaluate_tracks(self, log):\n        \"\"\"Evaluate the analyzed track data for deficiencies (actually split off the\n        logchecker-specific) stuff ;)\n        \"\"\"\n        pass\n\n    def deduct_and_score(self, log):\n        \"\"\"Process the accumulated deductions and score the log file.\"\"\"\n        if log.crc_mismatch:\n            log.add_deduction('CRC mismatch', len(log.crc_mismatch))\n\n        # Sum all the deductions and calculate score\n        log.score -= sum(\n            [de[1] for de in log.deductions.values() if isinstance(de[1], int)]\n        )\n","heybrochecklog/score/modules/combined.py":"\"\"\"This module contains the functions which deal with combined logs.\"\"\"\n\nimport re\n\nfrom heybrochecklog.logfile import LogFile\n\n\ndef split_combined(log):\n    \"\"\"Split a combined log into an array of logs. If there is a single\n    log, return a list with one log inside it. Not relevant for XLD.\n    \"\"\"\n    logs = []\n\n    # Create a list of indices for combined log markers. By default # includes indices 0 and len()\n    log_indices = (\n        [0]\n        + [\n            i + 2\n            for i, line in enumerate(log.full_contents)\n            if re.match(r'-{60}', line)\n        ]\n        + [len(log.full_contents)]\n    )\n\n    # Split the log files. Create new log object for each log.\n    for i, line in enumerate(log_indices):\n        new_log = LogFile(\n            log.full_contents[line : (log_indices[i + 1])], ripper=log.ripper\n        )\n        logs.append(new_log)\n\n        # Return the array of logs if the end index of section is\n        # equivalent to the length of the original log.\n        if log_indices[i + 1] == max(log_indices):\n            break\n\n    return logs\n\n\ndef defragment(logs, eac95=False):\n    \"\"\"Re-combine split combined logs.\"\"\"\n    if len(logs) == 1 and not logs[0].htoa:\n        return logs[0]\n\n    full_contents = []\n    for log in logs:\n        full_contents += log.full_contents\n    logs[0].full_contents = full_contents\n\n    # Make sure HTOA CRC's match if they exist.\n    if not eac95:\n        htoa_logs = [log for log in logs if log.htoa]\n        if htoa_logs:\n            analyze_htoa(logs, htoa_logs)\n\n    # Iterate through each log and patch the original log's data where new data exists.\n    # logs[0] is used as the base log and returned at the end.\n    for log in logs[1:]:\n        # Make sure the albums have the same name.\n        if log.album != logs[0].album:\n            return logs[0]\n\n        sub_settings(logs, log)\n        sub_double_copy(logs, log)\n        if not log.range:\n            sub_track_errors(logs, log)\n\n    # Remove HTOA detection if it was extracted.\n    if not eac95:\n        patch_htoa(logs, htoa_logs)\n\n    if len(logs) > 1:\n        logs[0].add_deduction('Combined log')\n\n    return logs[0]\n\n\ndef sub_settings(logs, log):\n    \"\"\"Check settings of newer rip; add new deductions and pop fixed deductions.\"\"\"\n    # Remove the deductions from the first log if they aren't present in the final log,\n    # but verify the number of tracks is consistent between the two.\n    for deduction in logs[0].deductions.copy():\n        if not log.has_deduction(deduction) and len(log.tracks) == len(logs[0].tracks):\n            logs[0].remove_deduction(deduction)\n    # Add additional deductions from the newer log to the older log.\n    for deduction in log.deductions.copy():\n        if not logs[0].has_deduction(deduction):\n            logs[0].add_deduction(deduction)\n\n\ndef sub_double_copy(logs, log):\n    \"\"\"Check two copy only rips and score as T&C Rip.\"\"\"\n    if logs[0].has_deduction('Test & Copy') and len(logs[0].tracks) == len(log.tracks):\n        for new_track, original_track in zip(\n            log.tracks.values(), logs[0].tracks.values()\n        ):\n            if 'copy crc' in new_track and 'copy crc' in original_track:\n                if new_track['copy crc'] != original_track['copy crc']:\n                    break\n        else:\n            logs[0].remove_deduction('Test & Copy')\n\n\ndef sub_track_errors(logs, log):\n    \"\"\"Substitute newer ripped track data for older ripped track data.\"\"\"\n    for track in log.tracks:\n        # Don't substitute track errors for aborted copies.\n        if 'copy crc' in log.tracks[track]:\n            logs[0].tracks[track] = log.tracks[track]\n            replace_accumulated_errors(track, logs, log)\n            replace_crc_mismatches(track, logs, log)\n\n\ndef replace_accumulated_errors(track, logs, log):\n    \"\"\"Replace accumulated track errors.\"\"\"\n    for error in logs[0].track_errors:\n        if track in log.track_errors[error]:\n            continue\n        for i, original_track in enumerate(logs[0].track_errors[error]):\n            if track == original_track:\n                logs[0].track_errors[error].pop(i)\n\n\ndef replace_crc_mismatches(track, logs, log):\n    \"\"\"Replace CRC mismatch errors.\"\"\"\n    if track not in log.crc_mismatch:\n        for i, original_track in enumerate(logs[0].crc_mismatch):\n            if track == original_track:\n                logs[0].crc_mismatch.pop(i)\n\n\ndef analyze_htoa(logs, htoa_logs):\n    \"\"\"Analyze potential HTOA rips to and add deductions for CRCs and T&C.\"\"\"\n    # Remove htoa logs from logs list\n    logs = [log for log in logs if log not in htoa_logs]\n\n    if len(htoa_logs) >= 2:\n        matches = [\n            log\n            for log in htoa_logs[1:]\n            if log.tracks[0]['copy crc'] == htoa_logs[0].tracks[0]['copy crc']\n        ]\n        if not matches:\n            htoa_logs[0].add_deduction('CRC mismatch on HTOA extraction')\n    elif len(htoa_logs) == 1:\n        htoa_logs[0].add_deduction('HTOA not ripped twice')\n\n    htoa_logs[0].remove_deduction('Test & Copy')\n    logs.append(htoa_logs[0])\n\n\ndef patch_htoa(logs, htoa_logs):\n    \"\"\"Adjust the HTOA deductions based on the defragmented log.\"\"\"\n    htoa_ripped = any(log for log in htoa_logs if log.htoa_ripped)\n\n    if htoa_ripped and logs[0].has_deduction('HTOA detected'):\n        logs[0].remove_deduction('HTOA detected not extracted')\n\n    if logs[0].has_deductions('Improper HTOA extraction', 'HTOA extracted'):\n        logs[0].remove_deduction('HTOA extracted')\n","heybrochecklog/score/modules/drives.py":"\"\"\"This module contains the functions which deal with drives and offsets.\"\"\"\n\nimport os\nimport re\nimport sqlite3\n\nfrom heybrochecklog import UnrecognizedException\nfrom heybrochecklog.shared import get_path\n\n\ndef eval_offset(log, offset):\n    \"\"\"Validate the offset used by the ripped drive.\"\"\"\n    if not re.match('-?[0-9]+', offset):\n        raise UnrecognizedException('Could not parse drive offset.')\n\n    if not offset.startswith('-') and offset != '0':\n        offset = '+' + offset\n\n    if check_for_virtual_drives(log):\n        log.add_deduction('Virtual drive')\n        log.flagged = True\n        return\n\n    drivestr = prep_drive_name(log)\n    if not drivestr:\n        return\n\n    results = drive_db_query(drivestr)\n    if not results:\n        # Drive not in database\n        if offset == '0':\n            log.add_deduction('Zero offset')\n        log.unindexed_drive = True\n        return\n\n    offsets = {row[0] for row in results}\n    if offset not in offsets:\n        log.add_deduction(\n            'Drive offset',\n            extra_phrase='correct offsets are: {}'.format(', '.join(offsets)),\n        )\n\n\ndef check_for_virtual_drives(log):\n    \"\"\"Check for usage of virtual drives; they aren't good and should be reported.\"\"\"\n    fake_drives = [\n        'Generic DVD-ROM SCSI CdRom Device'\n        # TODO: Compile a more comprehensive list.\n    ]\n    if log.drive in fake_drives:\n        return True\n    return False\n\n\ndef prep_drive_name(log):\n    \"\"\"Prepare the drive name for a DB query.\"\"\"\n    drive = sub_drive_names(log.drive)\n    drive_words = re.split(r'[^A-Za-z0-9]+', drive)\n    drivestr = '%\" AND Name LIKE \"%'.join(drive_words)\n\n    return drivestr\n\n\ndef sub_drive_names(drive):\n    \"\"\"Perform regex substitution actions on the drive name for better query results.\"\"\"\n    # Replace generic companies with real companies?\n    drive = re.sub(r'JLMS', 'Lite-ON', drive)\n    drive = re.sub(r'HL-DT-ST', 'LG Electronics', drive)\n    drive = re.sub(r'Matshita', 'MATSHITA', drive)\n    drive = re.sub(r'TSSTcorp(BD|CD|DVD)', r'TSSTcorp \\1', drive)\n    drive = re.sub(r'(\\s+-\\s|\\s+)', ' ', drive)\n    return drive\n\n\ndef drive_db_query(drivestr):\n    \"\"\"Query the SQLite3 DB for the drive offset.\"\"\"\n    db_path = os.path.join(get_path(), 'resources', 'drives.db')\n    conn = sqlite3.connect(db_path)\n\n    cursor = conn.cursor()\n    cursor.execute('SELECT Offset FROM Drives WHERE Name LIKE \"%' + drivestr + '%\"')\n    results = cursor.fetchall()\n\n    conn.close()\n\n    return results\n","heybrochecklog/score/modules/parsers.py":"\"\"\"This module contains functions which parse lines for data.\"\"\"\n\nimport re\n\nfrom heybrochecklog import UnrecognizedException\nfrom heybrochecklog.resources import VERSIONS\nfrom heybrochecklog.shared import format_pattern as fmt_ptn\n\n\ndef index_toc(log):\n    \"\"\"Index the ToC data of the log.\"\"\"\n    re_toc = re.compile(r' ([0-9]+) \\| [0-9:\\.]+ \\| [0-9:\\.]+ \\| ([0-9]+) \\| ([0-9]+)')\n    for line in log.contents[log.index_toc : log.index_tracks]:\n        result = re_toc.search(line)\n        if result:\n            log.toc[int(result.group(1))] = [int(result.group(2)), int(result.group(3))]\n\n\ndef get_track_number(log, index, track_word):\n    \"\"\"Get the track number from the header line of a track block.\"\"\"\n    result = re.search(r'{} ([0-9]+)'.format(fmt_ptn(track_word)), log.contents[index])\n    if result:\n        return int(result.group(1))\n    elif log.range:  # EAC range rip has no track number\n        return 0\n    else:\n        raise UnrecognizedException('A track has an invalid block header')\n\n\ndef parse_settings(track_data, track_settings, line):\n    \"\"\"Loop through and parse the settings used in the rip.\"\"\"\n    for setting, reg in track_settings.items():\n        result = reg.match(line)\n        if result:\n            track_data[setting] = result.group(1)\n\n\ndef parse_accuraterip(log, ar_patterns, line):\n    \"\"\"Parse line for an AccurateRip result.\"\"\"\n    for status, re_accurip in ar_patterns:\n        result = re.search(fmt_ptn(re_accurip), line)\n        if result and isinstance(result.lastindex, int) and result.lastindex >= 1:\n            log.accuraterip.append([status, result.group(result.lastindex)])\n        elif result and result.lastindex is None:\n            log.accuraterip.append([status, None])\n\n\ndef parse_range_accuraterip(log, ar_rr_patterns):\n    \"\"\"Parse range rip footer for AccurateRip results.\"\"\"\n    for line in log.contents[log.index_footer :]:\n        parse_accuraterip(log, ar_rr_patterns, line)\n\n\ndef parse_errors_eac(log, err_patterns, track_num, line):\n    \"\"\"Parse line of an EAC log for a ripping error.\"\"\"\n    for error, re_err in err_patterns:\n        if track_num not in log.track_errors[error] and re.match(\n            r' ' + fmt_ptn(re_err), line\n        ):\n            log.track_errors[error].append(track_num)\n\n\ndef parse_errors_xld(log, err_patterns, track_num, line):\n    \"\"\"Parse line of a XLD log for a ripping error.\"\"\"\n    for error, re_err in err_patterns:\n        if track_num not in log.track_errors[error]:\n            result = re.search(r' ' + fmt_ptn(re_err) + r' : ([0-9]+)', line)\n            if result and result.group(1) != \"0\":\n                log.track_errors[error].append([track_num, int(result.group(1))])\n\n\ndef parse_checksum(log, regex, imp_version, deduc_line):\n    \"\"\"Parse line(s) for presence of a checksum.\"\"\"\n    re_checksum = re.compile(fmt_ptn(regex))\n    for line in log.contents[log.index_footer :]:\n        if re_checksum.match(line):\n            log.checksum = True\n            break\n    else:  # If checksum not found\n        # Compare version numbers to see if Log is older than checksums.\n        for version in VERSIONS[log.ripper]:\n            if version[0] == log.version:\n                log_version = version\n            if version[0] == imp_version:\n                imp_version = version\n        if VERSIONS[log.ripper].index(log_version) <= VERSIONS[log.ripper].index(\n            imp_version\n        ):\n            log.add_deduction('Checksum')\n        else:\n            log.add_deduction(deduc_line + ' (no checksum)')\n","heybrochecklog/score/modules/validation.py":"\"\"\"This module contains validation functions for log checking.\"\"\"\n\nfrom heybrochecklog import UnrecognizedException\n\n\ndef analyze_accuraterip(log):\n    \"\"\"Analyze the AccurateRip results in the log.\"\"\"\n    if log.accuraterip:\n        for ar_result in log.accuraterip:\n            if (\n                ar_result[0] != log.accuraterip[0][0]\n                and (ar_result[1] is not None and log.accuraterip[0][1] is not None)\n                and (int(ar_result[1]) >= 5 or int(log.accuraterip[0][1]) >= 5)\n            ):\n                log.add_deduction('AccurateRip discrepancies')\n                break\n    elif any('copy crc' in data for tnum, data in log.tracks.items()):\n        log.add_deduction('AccurateRip')\n\n\ndef check_crc_mismatch(log, track_num, track_data):\n    \"\"\"Check a track block for a CRC mismatch.\"\"\"\n    if all(data in track_data for data in ['test crc', 'copy crc']):\n        if track_data['test crc'] != track_data['copy crc']:\n            log.crc_mismatch.append(track_num)\n\n\ndef validate_track_count(log):\n    \"\"\"Verify the presence of all tracks and check for a data track.\"\"\"\n    if not log.range:\n        # Data tracks have one extra ToC entry, but one less ripped track\n        if max(log.tracks.keys()) + 1 == max(log.toc.keys()):\n            log.add_deduction('Data track detected')\n        elif len(log.tracks) != len(log.toc):\n            if not log.ripper == 'XLD' or not log.has_deduction('HTOA extracted'):\n                raise UnrecognizedException('Not all tracks are represented in the log')\n\n\ndef validate_track_settings(log, xld=False):\n    \"\"\"Also verify that each track contains the required data.\"\"\"\n    if xld:\n        if log.range:\n            required_settings = ['copy crc']\n        else:\n            required_settings = ['filename', 'copy crc']\n    else:\n        required_settings = ['filename', 'peak', 'copy crc']\n\n    for track in log.tracks:\n        if track in log.track_errors['Aborted copy']:\n            pass\n        elif not all(setting in log.tracks[track] for setting in required_settings):\n            raise UnrecognizedException(\n                'Unable to confirm presence of required track data'\n            )\n\n    # Check for Test & Copy and CRC Mismatches\n    if not all('test crc' in track for track in log.tracks.values()):\n        if log.has_deduction('HTOA extracted') or log.has_deduction(\n            'HTOA not ripped twice'\n        ):\n            # Verify that every track minus HTOA is T&C (Range based is index 0)\n            if all('test crc' in track for i, track in log.tracks.items() if i):\n                return\n        log.add_deduction('Test & Copy')\n","heybrochecklog/score/eac95.py":"\"\"\"This module contains the EAC Version <=0.95 Log Checker.\"\"\"\n\nimport re\n\nfrom heybrochecklog import UnrecognizedException\ndef markup(*args, **kwargs):\n    return None\nfrom heybrochecklog.score.logchecker import LogChecker\nfrom heybrochecklog.score.modules import combined, drives, parsers, validation\nfrom heybrochecklog.shared import format_pattern as fmt_ptn\n\n\nclass EAC95Checker(LogChecker):\n    \"\"\"This class analyzes <=0.95 EAC Log Files.\"\"\"\n\n    def check(self, main_log):\n        \"\"\"Checks the EAC logs.\"\"\"\n        logs = combined.split_combined(main_log)\n        for log in logs:\n            if len(log.concat_contents) < 12:\n                raise UnrecognizedException('Cannot parse log file; log file too short')\n\n            log.version = 'EAC <=0.95'\n            log.album = log.concat_contents[1]\n            log.drive = self.check_drive(log)\n\n            self.index_log(log, ninety_five=True)\n            self.evaluate_settings(log)\n            self.check_tracks(log)\n            if self.markup:\n                markup(log, self.patterns, self.translation)\n\n        main_log = combined.defragment(logs, eac95=True)\n        validation.validate_track_settings(main_log)\n        self.deduct_and_score(main_log)\n\n        return main_log\n\n    def check_drive(self, log):\n        \"\"\"Check the drive of the log and verify it is an allowed drive.\"\"\"\n        regex = r' ?: (.*) Adapter:[ 0-9]+ID:[ 0-9]+$'\n        return self.get_drive(regex, log.concat_contents[2])\n\n    def all_range_index(self, log, line):\n        \"\"\"Match the Range Rip line in the log file.\"\"\"\n        if log.index_tracks is None and re.match(fmt_ptn(self.patterns['range']), line):\n            return True\n        return False\n\n    def all_range_index_action(self, log, line_num):\n        \"\"\"Action to take when the range rip line is matched.\"\"\"\n        log.track_indices.append(line_num)\n        log.range = True\n\n    def evaluate_settings(self, log):\n        \"\"\"Evaluate the log for usage of proper rip settings.\n        Overwriting the base class for different 0.95 behavior.\n        \"\"\"\n        psettings = self.patterns['settings']\n        full_psettings = self.patterns['full line settings']\n        proper_settings = self.patterns['proper settings']\n\n        # Compile regex beforehand\n        settings, full_settings = {}, {}\n        for key, regex in psettings.items():\n            settings[key] = re.compile(fmt_ptn(regex))\n        for key, regex in full_psettings.items():\n            full_settings[key] = re.compile(fmt_ptn(regex) + ' : (.*)')\n\n        # Iterate through line in the settings, and verify each setting in `settings` dict\n        for line in log.contents[log.index_settings : log.index_tracks]:\n            for key, setting in list(settings.items()):\n                result = setting.search(line)\n                if result:\n                    if key == 'Drive offset':\n                        offset = re.search(r'.+: ([-0-9]+)', line)\n                        drives.eval_offset(log, offset.group(1))\n                    del settings[key]\n            for key, setting in list(full_settings.items()):\n                result = setting.search(line)\n                if result:\n                    if not re.search(fmt_ptn(proper_settings[key]), result.group(1)):\n                        log.add_deduction(key)\n                    del full_settings[key]\n                    break\n\n            self.check_bad_settings(log, line)\n\n        self.evaluate_unmatched_settings(log, settings)\n\n    def check_offset(self, log, line, off_settings):\n        \"\"\"Check a log file line for proper offset.\"\"\"\n        found = False\n        for key, setting in off_settings.items():\n            result = setting.search(line)\n            if result:\n                if key == 'Combined offset':\n                    log.add_deduction('Combined offset')\n                else:\n                    drives.eval_offset(log, result.group(1))\n                found = True\n\n        return found\n\n    def check_bad_settings(self, log, line):\n        \"\"\"Evaluate the instant -100 point deductions\n        (destructive normalization and compression offset).\"\"\"\n        bad_settings = self.patterns['bad settings']\n        for sett, pattern in bad_settings.items():\n            if re.search(fmt_ptn(pattern), line):\n                log.add_deduction(sett)\n\n    def evaluate_unmatched_settings(self, log, settings):\n        \"\"\"Evaluate all unmatched settings and deduct for them.\n        <=0.95 is using a match/no match string algorithm, so it's a deduction if no match.\"\"\"\n        if log.has_deduction('Combined offset') and 'Drive offset' in settings:\n            del settings['Drive offset']\n        for key in settings:\n            log.add_deduction(key)\n\n    def check_tracks(self, log):\n        \"\"\"Get track data for each track and check for errors.\"\"\"\n        tsettings = self.patterns['track settings']\n        track_settings = {\n            'filename': re.compile(r' ' + fmt_ptn(tsettings['filename']) + r' (.*)'),\n            'pregap': re.compile(r' ' + fmt_ptn(tsettings['pregap']) + r' ([0-9:\\.]+)'),\n            'peak': re.compile(r' ' + fmt_ptn(tsettings['peak']) + r' ([0-9\\.])+ %'),\n            'test crc': re.compile(\n                r' ' + fmt_ptn(tsettings['test crc']) + r' ([A-Z0-9]{8})'\n            ),\n            'copy crc': re.compile(\n                r' ' + fmt_ptn(tsettings['copy crc']) + r' ([A-Z0-9]{8})'\n            ),\n        }\n\n        self.analyze_tracks(\n            log, track_settings, parsers.parse_errors_eac, accuraterip=False\n        )\n\n    def evaluate_tracks(self, log):\n        \"\"\"Evaluate the analyzed track data for deficiencies.\"\"\"\n        # Deduct for a Range Rip\n        if log.range:\n            log.add_deduction('Range rip')\n\n    def deduct_and_score(self, log):\n        \"\"\"Process the accumulated deductions and score the log file.\"\"\"\n        # EAC <=0.95 mandatory deductions.\n        log.add_deduction('EAC 0.95')\n        log.add_deduction('EAC <1.0 (no checksum)')\n        if not log.range:\n            log.add_deduction('Gap handling')\n        else:\n            log.add_deduction('Could not verify gap handling')\n\n        # Deduct for all the per-track accumulated deductions.\n        for error in log.track_errors:\n            if log.track_errors[error]:\n                log.add_deduction(error, len(log.track_errors[error]))\n\n        super().deduct_and_score(log)\n","heybrochecklog/resources/eac95/english.json":"{\n    \"patterns\": {\n        \"drive\": [\"Used [Dd]rive\"],\n        \"settings\": {\n            \"Read mode\": [\"Secure\"],\n            \"C2 pointers\": [\"with NO C2\"],\n            \"Accurate stream\": [\", accurate stream\"],\n            \"Audio cache\": [\", disable cache\"],\n            \"Drive offset\": [\"Read offset correction\"]\n        },\n        \"full line settings\": {\n            \"Fill missing offset samples with silence\": [\"Fill up missing offset samples with silence\"],\n            \"Deleting silent blocks\": [\"Delete leading and trailing silent blocks\"]\n        },\n        \"bad settings\": {\n            \"Normalization\": [\"Normalize to\"],\n            \"Compression offset\": [\"Use compression offset\"],\n            \"Combined offset\": [\"Combined read/write offset correction\"]\n        },\n        \"proper settings\": {\n            \"Fill missing offset samples with silence\": [\"Yes\"],\n            \"Deleting silent blocks\": [\"No\"]\n        },\n        \"range\": [\"Range status and errors\"],\n        \"track\": [\"Track\"],\n        \"track settings\": {\n            \"filename\": [\"Filename\"],\n            \"pregap\": [\"Pre-gap length\"],\n            \"peak\": [\"Peak level\"],\n            \"test crc\": [\"Test CRC\"],\n            \"copy crc\": [\"(?:Copy CRC|CRC)\"]\n        },\n        \"track errors\": {\n            \"Aborted copy\": [\"Copy aborted\"],\n            \"Timing problem\": [\"Timing problem\"],\n            \"Missing samples\": [\"Missing samples\"],\n            \"Suspicious position\": [\"Suspicious position\"]\n        },\n        \"footer\": [\"End of status report\"]\n    },\n    \"translation\": {\n        \"1\": [\"English\"],\n        \"2\": [\"English\"],\n        \"5\": [\"Error Message\"],\n        \"6\": [\"Warning\"],\n        \"7\": [\"Success\"],\n        \"10\": [\"OK\"],\n        \"11\": [\"Cancel\"],\n        \"12\": [\"Apply\"],\n        \"15\": [\"Yes\"],\n        \"16\": [\"No\"],\n        \"31\": [\"Filename will be ignored\"],\n        \"50\": [\"Value out of range !\"],\n        \"51\": [\"Invalid characters !\"],\n        \"52\": [\"Invalid filename !\"],\n        \"4270\": [\"Low\"],\n        \"4271\": [\"Medium\"],\n        \"4272\": [\"High\"],\n        \"1200\": [\"Status and Error Messages\"],\n        \"2501\": [\"Track status and errors\"],\n        \"1203\": [\"Possible Errors\"],\n        \"1204\": [\"Create Log\"],\n        \"1210\": [\"Range status and errors\"],\n        \"1211\": [\"Selected range\"],\n        \"1212\": [\"Timing problem\"],\n        \"1213\": [\"Suspicious position\"],\n        \"1214\": [\"Missing samples\"],\n        \"1215\": [\"Too many samples\"],\n        \"1216\": [\"File write error\"],\n        \"1217\": [\"Peak level\"],\n        \"1218\": [\"Range quality\"],\n        \"1219\": [\"CRC\"],\n        \"1220\": [\"Copy OK\"],\n        \"1221\": [\"Copy finished\"],\n        \"1227\": [\"Track quality\"],\n        \"1228\": [\"Copy aborted\"],\n        \"1269\": [\"Filename\"],\n        \"1270\": [\"Pre-gap length\"],\n        \"1271\": [\"Test CRC\"],\n        \"1272\": [\"Copy CRC\"],\n        \"1273\": [\"Compressing\"],\n        \"1222\": [\"No errors occured\"],\n        \"1223\": [\"Review Range\"],\n        \"1224\": [\"There were errors\"],\n        \"1225\": [\"End of status report\"],\n        \"1226\": [\"Track\"],\n        \"1230\": [\"Index\"],\n        \"1229\": [\"Review Tracks\"],\n        \"1274\": [\"EAC extraction logfile from\"],\n        \"1240\": [\"January\"],\n        \"1241\": [\"February\"],\n        \"1242\": [\"March\"],\n        \"1243\": [\"April\"],\n        \"1244\": [\"May\"],\n        \"1245\": [\"June\"],\n        \"1246\": [\"July\"],\n        \"1247\": [\"August\"],\n        \"1248\": [\"September\"],\n        \"1249\": [\"October\"],\n        \"1250\": [\"November\"],\n        \"1251\": [\"December\"],\n        \"1232\": [\"EAC extraction logfile for CD\"],\n        \"1233\": [\"Used drive  :\"],\n        \"1234\": [\"Read mode   :\"],\n        \"1235\": [\"Burst\"],\n        \"1236\": [\"Fast\"],\n        \"1237\": [\"Secure with NO C2, NO accurate stream, NO disable cache\"],\n        \"1238\": [\"Secure with NO C2, accurate stream, NO disable cache\"],\n        \"1239\": [\"Secure with NO C2, NO accurate stream, disable cache\"],\n        \"1252\": [\"Secure with C2, accurate stream, NO disable cache\"],\n        \"1253\": [\"Secure with C2, accurate stream, disable cache\"],\n        \"1254\": [\"Secure with NO C2, accurate stream, disable cache\"],\n        \"1255\": [\"Combined read/write offset correction :\"],\n        \"1256\": [\"Read offset correction :\"],\n        \"1257\": [\"Overread into Lead-In and Lead-Out\"],\n        \"1258\": [\"Used output format :\"],\n        \"1259\": [\"Additional command line options :\"],\n        \"1260\": [\"Internal WAV Routines\"],\n        \"1261\": [\"44.100 Hz; 16 Bit; Stereo\"],\n        \"1262\": [\"Use compression offset :\"],\n        \"1263\": [\"Other options      :\"],\n        \"1264\": [\"Fill up missing offset samples with silence\"],\n        \"1265\": [\"Delete leading and trailing silent blocks\"],\n        \"1266\": [\"Normalize to\"],\n        \"1267\": [\"Native Win32 interface for Win NT & 2000\"],\n        \"1268\": [\"Installed external ASPI interface\"],\n        \"81700\": [\"L3Enc MP3 Encoder & Compatible\"],\n        \"81701\": [\"Fraunhofer MP3Enc MP3 Encoder\"],\n        \"81702\": [\"Xing X3Enc MP3 Encoder\"],\n        \"81703\": [\"Xing ToMPG MP3 Encoder\"],\n        \"81704\": [\"LAME MP3 Encoder\"],\n        \"81705\": [\"GOGO MP3 Encoder\"],\n        \"81706\": [\"MPC Encoder\"],\n        \"81707\": [\"Ogg Vorbis Encoder\"],\n        \"81708\": [\"Microsoft WMA9 Encoder\"],\n        \"81709\": [\"FAAC AAC Encoder\"],\n        \"81710\": [\"Homeboy AAC Encoder\"],\n        \"81711\": [\"Quartex AAC Encoder\"],\n        \"81712\": [\"PsyTEL AAC Encoder\"],\n        \"81713\": [\"MBSoft AAC Encoder\"],\n        \"81714\": [\"Yamaha VQF Encoder\"],\n        \"81715\": [\"Real Audio Encoder\"],\n        \"81716\": [\"Monkey's Audio Lossless Encoder\"],\n        \"81717\": [\"Shorten Lossless Encoder\"],\n        \"81718\": [\"RKAU Lossless Encoder\"],\n        \"81719\": [\"LPAC Lossless Encoder\"],\n        \"81720\": [\"User Defined Encoder\"]\n    }\n}\n"}
LOGCHECKER_RESULT_CACHE = DATA / 'cache' / 'heybrochecklog-results-v6.json'
_LOGCHECKER_LOCK = threading.RLock()
_HEYBRO_SCORE_LOG = None
_HEYBRO_LAST_ERROR = None


def _write_vendored_heybro_tree(root: Path):
    for relative, content in VENDORED_HEYBRO_FILES.items():
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding='utf-8', newline='\n')

    import sqlite3
    db_path = root / 'heybrochecklog' / 'resources' / 'drives.db'
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            'CREATE TABLE IF NOT EXISTS Drives ('
            'DriveID INTEGER NOT NULL PRIMARY KEY, '
            'Name TEXT NOT NULL, '
            'Offset TEXT NOT NULL)'
        )
        connection.commit()
    finally:
        connection.close()


def _vendored_heybro_root() -> Path:
    root = TEMP / (
        'embedded-hey-bro-check-log-' +
        HEYBROCHECKLOG_COMMIT[:12] + '-' +
        HEYBROCHECKLOG_BUNDLE_REVISION
    )
    marker = root / '.ready'
    expected = HEYBROCHECKLOG_COMMIT + ':' + HEYBROCHECKLOG_BUNDLE_REVISION
    try:
        current = marker.read_text('utf-8').strip() if marker.is_file() else ''
    except Exception:
        current = ''
    if current != expected:
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        _write_vendored_heybro_tree(root)
        marker.write_text(expected, encoding='utf-8')
    return root


def ensure_heybrochecklog(force=False):
    global _HEYBRO_SCORE_LOG, _HEYBRO_LAST_ERROR
    with _LOGCHECKER_LOCK:
        if force:
            _HEYBRO_SCORE_LOG = None
            for name in list(sys.modules):
                if name == 'heybrochecklog' or name.startswith('heybrochecklog.'):
                    sys.modules.pop(name, None)

        if _HEYBRO_SCORE_LOG is not None:
            return _HEYBRO_SCORE_LOG

        try:
            root = _vendored_heybro_root()
            root_text = str(root)
            if root_text not in sys.path:
                sys.path.insert(0, root_text)

            for name in list(sys.modules):
                if name == 'heybrochecklog' or name.startswith('heybrochecklog.'):
                    sys.modules.pop(name, None)

            importlib.invalidate_caches()
            from heybrochecklog.score import score_log
            module_file = str(getattr(sys.modules.get('heybrochecklog'), '__file__', ''))
            if root_text.casefold() not in module_file.casefold():
                raise RuntimeError('Loaded heybrochecklog from unexpected location: ' + module_file)

            _HEYBRO_SCORE_LOG = score_log
            _HEYBRO_LAST_ERROR = None
            LOG.event(
                'embedded_heybrochecklog_ready',
                version=HEYBROCHECKLOG_PACKAGE_VERSION,
                commit=HEYBROCHECKLOG_COMMIT,
                module=module_file,
            )
            return _HEYBRO_SCORE_LOG
        except Exception as exc:
            _HEYBRO_LAST_ERROR = f'{type(exc).__name__}: {exc}'
            LOG.error('embedded_heybrochecklog_failed', exc)
            return None


def clearly_supported_xld_log(path: Path) -> bool:
    try:
        with path.open('r', encoding='utf-8-sig', errors='replace') as fh:
            first = fh.readline().strip()
        match = re.fullmatch(
            r'X Lossless Decoder version ([0-9abc]+) \(([0-9.]+)\)',
            first,
        )
        return bool(match and re.fullmatch(r'20\d{6}[a-z]?', match.group(1)))
    except Exception:
        return False


ensure_heybrochecklog()

def read_logchecker_cache():
    try:
        data = json.loads(LOGCHECKER_RESULT_CACHE.read_text('utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_logchecker_cache(data):
    try:
        LOGCHECKER_RESULT_CACHE.parent.mkdir(parents=True, exist_ok=True)
        temp = LOGCHECKER_RESULT_CACHE.with_suffix('.tmp')
        temp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
        temp.replace(LOGCHECKER_RESULT_CACHE)
    except Exception as exc:
        LOG.error('logchecker_cache_write_failed', exc)


def analyze_logchecker_file(path: Path) -> dict:
    try:
        stat = path.stat()
        raw_log = path.read_bytes()
        content_sha256 = hashlib.sha256(raw_log).hexdigest()
        key = str(path.resolve()).casefold()
        with _LOGCHECKER_LOCK:
            cache = read_logchecker_cache()
            old = cache.get(key, {})
            same_file = (
                old.get('size') == stat.st_size and
                old.get('mtime_ns') == stat.st_mtime_ns and
                old.get('checker') == 'ligh7s/hey-bro-check-log' and
                old.get('checker_commit') == HEYBROCHECKLOG_COMMIT and
                old.get('content_sha256') == content_sha256
            )
            if same_file and time.time() - float(old.get('checked_at', 0) or 0) < 30 * 86400:
                return dict(old)

        score_log = ensure_heybrochecklog()
        if not score_log:
            result = {
                'path': str(path),
                'size': stat.st_size,
                'mtime_ns': stat.st_mtime_ns,
                'score': None,
                'ripper': None,
                'ripper_version': None,
                'deductions': [],
                'flagged': False,
                'recognized': False,
                'content_sha256': content_sha256,
                'error': ('Embedded checker unavailable: ' + (_HEYBRO_LAST_ERROR or 'unknown error')),
                'checker': 'ligh7s/hey-bro-check-log',
                'checker_commit': HEYBROCHECKLOG_COMMIT,
                'checker_version': HEYBROCHECKLOG_PACKAGE_VERSION,
                'checked_at': time.time(),
            }
        else:
            try:
                parsed = score_log(path, False)
                unrecognized = parsed.get('unrecognized')
                score = None if unrecognized else parsed.get('score')
                result = {
                    'path': str(path),
                    'size': stat.st_size,
                    'mtime_ns': stat.st_mtime_ns,
                    'score': float(score) if score is not None else None,
                    'ripper': parsed.get('ripper'),
                    'ripper_version': parsed.get('version'),
                    'deductions': parsed.get('deductions') or [],
                    'flagged': bool(parsed.get('flagged')),
                    'recognized': not bool(unrecognized),
                    'content_sha256': content_sha256,
                    'error': (
                        f'Parser rejected log: {unrecognized}'
                        if unrecognized else None
                    ),
                    'checker': 'ligh7s/hey-bro-check-log',
                'checker_commit': HEYBROCHECKLOG_COMMIT,
                    'checker_version': HEYBROCHECKLOG_PACKAGE_VERSION,
                    'checked_at': time.time(),
                }
            except Exception as exc:
                result = {
                    'path': str(path),
                    'size': stat.st_size,
                    'mtime_ns': stat.st_mtime_ns,
                    'score': None,
                    'ripper': None,
                    'ripper_version': None,
                    'deductions': [],
                    'flagged': False,
                    'recognized': False,
                    'content_sha256': content_sha256,
                    'error': f'Checker exception: {type(exc).__name__}: {exc}',
                    'checker': 'ligh7s/hey-bro-check-log',
                'checker_commit': HEYBROCHECKLOG_COMMIT,
                    'checker_version': HEYBROCHECKLOG_PACKAGE_VERSION,
                    'checked_at': time.time(),
                }

        with _LOGCHECKER_LOCK:
            cache = read_logchecker_cache()
            cache[key] = result
            write_logchecker_cache(cache)

        LOG.event(
            'logchecker_file_result',
            path=str(path),
            score=result.get('score'),
            ripper=result.get('ripper'),
            deductions=result.get('deductions'),
            flagged=result.get('flagged'),
            recognized=result.get('recognized'),
            content_sha256=result.get('content_sha256'),
            error=result.get('error'),
            checker=result.get('checker'),
            checker_version=result.get('checker_version'),
        )
        return result
    except Exception as exc:
        LOG.error('logchecker_file_failed', exc, path=str(path))
        return {
            'path': str(path),
            'score': None,
            'recognized': False,
            'content_sha256': None,
            'error': f'Checker exception: {type(exc).__name__}: {exc}',
            'checker': 'ligh7s/hey-bro-check-log',
            'checker_commit': HEYBROCHECKLOG_COMMIT,
        }


def release_disc_numbers(release) -> list[int]:
    numbers = set()
    for track in getattr(release, 'tracks', []) or []:
        match = re.search(r'\d+', str(getattr(track, 'discno', '') or ''))
        if match:
            try:
                numbers.add(int(match.group(0)))
            except Exception:
                pass

    if not numbers:
        match = re.search(
            r'(?i)(?:^|[^a-z0-9])(?:cd|disc|disk)\s*[-_ ]*0*(\d+)(?:[^0-9]|$)',
            str(getattr(release, 'name', '') or ''),
        )
        if match:
            try:
                numbers.add(int(match.group(1)))
            except Exception:
                pass
    return sorted(numbers)


def discover_release_log_paths(release) -> list[Path]:
    root = Path(getattr(release, 'folder', '') or '')
    if not root.is_dir():
        return []

    try:
        all_logs = sorted(
            path for path in root.rglob('*.log')
            if path.is_file()
            and not path.name.casefold().startswith('logchecker')
            and path.name.casefold() not in {'audiochecker.log', 'audio checker.log'}
        )
    except Exception as exc:
        LOG.error('logchecker_log_discovery_failed', exc, folder=str(root))
        return []

    if not all_logs:
        return []

    discs = release_disc_numbers(release)
    if not discs:
        return all_logs

    matched = []
    for path in all_logs:
        stem = path.stem
        for disc in discs:
            pattern = (
                rf'(?i)(?:^|[^a-z0-9])(?:cd|disc|disk)\s*[-_ ]*0*{disc}'
                rf'(?:[^0-9]|$)'
            )
            if re.search(pattern, stem):
                matched.append(path)
                break

    if matched:
        return sorted(dict.fromkeys(matched))
    if len(all_logs) == 1:
        return all_logs
    return all_logs


def score_release_logs(release) -> tuple[float | None, list[dict]]:
    log_paths = discover_release_log_paths(release)
    if not log_paths:
        return None, []

    results = [analyze_logchecker_file(path) for path in log_paths]
    scores = [
        float(row['score'])
        for row in results
        if row.get('score') is not None
    ]
    score = round(sum(scores) / len(scores), 2) if scores else None
    return score, results



COPY_NAME_RE = re.compile(
    r'(?i)(?:\s*[\(\[]\d+[\)\]]|\s*-\s*copy(?:\s*\(\d+\)|\s+\d+)?|\s+copy(?:\s*\(\d+\)|\s+\d+)?)$'
)


def release_log_fingerprints(release) -> tuple[str, ...]:
    fingerprints = [
        str(row.get('content_sha256') or '').strip().lower()
        for row in (getattr(release, 'logchecker_logs', []) or [])
    ]
    if not fingerprints or any(not value for value in fingerprints):
        return ()
    return tuple(sorted(fingerprints))


def copy_name_penalty(value: str) -> int:
    name = Path(str(value or '')).name.strip()
    return 1 if COPY_NAME_RE.search(name) else 0


def duplicate_copy_rank(release) -> tuple:
    log_penalty = sum(
        copy_name_penalty(row.get('path') or '')
        for row in (getattr(release, 'logchecker_logs', []) or [])
    )
    folder = str(getattr(release, 'folder', '') or '')
    return (
        copy_name_penalty(folder) + log_penalty,
        len(folder),
        folder.casefold(),
        str(getattr(release, 'rid', '') or ''),
    )

def logchecker_display(release) -> str:
    logs = getattr(release, 'logchecker_logs', []) or []
    score = getattr(release, 'logchecker_score', None)
    if score is not None:
        suffix = 'log' if len(logs) == 1 else 'logs'
        if len(logs) <= 1:
            return f'{score:g} ({len(logs)} {suffix})'
        return f'{score:g} avg ({len(logs)} {suffix})'
    if logs:
        errors = [str(row.get('error') or '').strip() for row in logs if row.get('error')]
        if errors:
            first = errors[0]
            if len(first) > 120:
                first = first[:117] + '...'
            return f'ERROR: {first}'
        return f'no score ({len(logs)} logs)'
    return 'no rip log'


import tkinter as tk
from tkinter import ttk, filedialog, messagebox


AUDIO = {
    '.flac', '.mp3', '.m4a', '.mp4', '.aac', '.ogg', '.opus', '.wav', '.wma',
    '.ape', '.wv', '.tta', '.mka', '.dsf', '.dff'
}


def norm(value: str) -> str:
    value = (value or '').casefold().replace('’', "'")
    value = re.sub(r'\s+', ' ', value).strip()
    return re.sub(r"[^\w\s'\-\(\)\[\]]+", '', value)


def clean_identifier(value: str) -> str:
    value = norm(value)
    return re.sub(r'\s+', '', value)


def compact_text(value: str) -> str:
    value = (value or '').casefold().replace('’', "'")
    return ''.join(ch for ch in value if ch.isalnum())


def scalar_text(value) -> str:
    if value is None:
        return ''
    if hasattr(value, 'text'):
        return scalar_text(value.text)
    if hasattr(value, 'data') and isinstance(getattr(value, 'data', None), (bytes, bytearray, memoryview)):
        return scalar_text(value.data)
    if isinstance(value, (list, tuple)):
        for item in value:
            text_value = scalar_text(item)
            if text_value:
                return text_value
        return ''
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        for encoding in ('utf-8', 'utf-16', 'latin-1'):
            try:
                return raw.decode(encoding).replace('\x00', '').strip()
            except Exception:
                continue
        return ''
    return str(value).replace('\x00', '').strip()


def first(tags, *keys) -> str:
    for key in keys:
        try:
            value = tags.get(key)
            text_value = scalar_text(value)
            if text_value:
                return text_value
        except Exception:
            pass
    return ''


def tag(tags, name: str) -> str:
    aliases = {
        'title': ('title', 'TIT2', '©nam'),
        'artist': ('artist', 'TPE1', '©ART'),
        'album': ('album', 'TALB', '©alb'),
        'albumartist': ('albumartist', 'album artist', 'TPE2', 'aART'),
        'date': ('date', 'year', 'TDRC', '©day'),
        'barcode': ('barcode', 'BARCODE', '----:com.apple.iTunes:BARCODE'),
        'isrc': ('isrc', 'TSRC', '----:com.apple.iTunes:ISRC'),
        'mbrec': ('musicbrainz_recordingid', 'MUSICBRAINZ_TRACKID', 'UFID:http://musicbrainz.org'),
        'mbrel': ('musicbrainz_albumid', 'MUSICBRAINZ_ALBUMID'),
        'disc': ('discnumber', 'TPOS', 'disk'),
        'track': ('tracknumber', 'TRCK', 'trkn'),
    }
    return first(tags, *aliases[name])


def version_hint(title: str) -> str:
    pieces = re.findall(
        r'[\(\[]([^\)\]]*(?:mix|remix|edit|version|live|acoustic|instrumental|radio|extended|dub|demo|remaster|karaoke)[^\)\]]*)[\)\]]',
        title or '',
        re.I,
    )
    return norm(' '.join(pieces))


REMIX_RE = re.compile(
    r'(?<![a-z0-9])(?:remix(?:es|ed)?|rmx|dub|mashup|bootleg)(?![a-z0-9])',
    re.I,
)
LIVE_RE = re.compile(
    r'(?<![a-z0-9])live(?:\s+acoustic)?(?![a-z0-9])',
    re.I,
)
SNIPPET_RE = re.compile(
    r'(?<![a-z0-9])(?:snippet|excerpt|preview|callout|hook)(?![a-z0-9])',
    re.I,
)
FEATURE_RE = re.compile(
    r'(?<![a-z0-9])(?:feat(?:uring)?\.?|ft\.?)\s+',
    re.I,
)


def ignored_track_reason(title: str, artist: str, relative_path: str) -> str:
    title = title or ''
    artist = artist or ''

    if SNIPPET_RE.search(title):
        return 'snippet'

    path_parts = re.split(r'[\\/]+', relative_path or '')
    if any(SNIPPET_RE.search(part) for part in path_parts[:-1]):
        return 'snippet'

    if LIVE_RE.search(title):
        return 'live recording'

    if REMIX_RE.search(title):
        if FEATURE_RE.search(title) or FEATURE_RE.search(artist):
            return ''
        return 'remix'

    return ''


def raw_track_identity(track) -> str:
    mbid = clean_identifier(getattr(track, 'mbrec', '') or '')
    if mbid:
        return 'mb:' + mbid
    isrc = clean_identifier(getattr(track, 'isrc', '') or '')
    if isrc:
        return 'isrc:' + isrc
    return (
        'meta:' +
        compact_text(getattr(track, 'artist', '') or '') + ':' +
        compact_text(getattr(track, 'title', '') or '')
    )


def raw_track_signature(release) -> tuple:
    return tuple(sorted(Counter(
        raw_track_identity(track)
        for track in getattr(release, 'tracks', []) or []
    ).items()))


def exact_audio_duplicate_signature(release) -> tuple:
    rows = []
    for track in getattr(release, 'tracks', []) or []:
        path = Path(track.path)
        try:
            size = path.stat().st_size
        except Exception:
            size = 0
        rows.append((
            raw_track_identity(track),
            int(round(float(track.duration or 0) * 10.0)),
            path.suffix.casefold(),
            size,
            track.ignored_reason,
        ))
    return tuple(sorted(rows))


DISC_FOLDER_RE = re.compile(r'^(?:cd|disc|disk)\s*[-_ ]?\s*0*\d+(?:\s*(?:of|/)\s*\d+)?$', re.I)
ORGANIZATIONAL_FOLDER_RE = re.compile(
    r'^\d+\.\s*(?:albums?|compilations?|singles?|soundtracks?|bootlegs?|remixes?|tracks?)$',
    re.I,
)


def is_disc_folder(path: Path) -> bool:
    return bool(DISC_FOLDER_RE.fullmatch(path.name.strip()))


def is_organizational_container(path: Path) -> bool:
    return bool(ORGANIZATIONAL_FOLDER_RE.fullmatch(path.name.strip()))


def base_album_name(value: str) -> str:
    value = (value or '').strip()
    patterns = (
        r'\s*[:\-]?\s*(?:cd|disc|disk)\s*0*\d+\s*$',
        r'\s*\((?:cd|disc|disk)\s*0*\d+\)\s*$',
        r'\s*\[(?:cd|disc|disk)\s*0*\d+\]\s*$',
    )
    previous = None
    while value and value != previous:
        previous = value
        for pattern in patterns:
            value = re.sub(pattern, '', value, flags=re.I).strip()
    return value


def release_folder_for(path: Path, root: Path) -> Path:
    parent = path.parent
    while parent != root and is_disc_folder(parent):
        parent = parent.parent
    return parent


def source_container_for(release_folder: Path, root: Path) -> str:
    try:
        rel = release_folder.relative_to(root)
        if len(rel.parts) >= 2:
            return rel.parts[0]
    except Exception:
        pass
    return root.name


def classify_media_type(release_folder: Path, source_container: str) -> tuple[str, str]:
    name_text = release_folder.name.casefold()
    source_text = (source_container or '').casefold()

    explicit_web = bool(re.search(r'(?<![a-z0-9])web(?![a-z0-9])', name_text))
    explicit_cd = bool(re.search(r'(?<![a-z0-9])(?:\d+\s*)?cd(?![a-z0-9])', name_text))
    source_web = source_text in {'deemix', 'deezer'}

    has_cue = False
    has_log = False
    try:
        candidates = list(release_folder.iterdir())
        for child in list(candidates):
            if child.is_dir() and is_disc_folder(child):
                try:
                    candidates.extend(child.iterdir())
                except Exception:
                    pass
        for item in candidates:
            if not item.is_file():
                continue
            suffix = item.suffix.casefold()
            if suffix == '.cue':
                has_cue = True
            elif suffix == '.log':
                has_log = True
    except Exception:
        pass

    if explicit_web and explicit_cd:
        return 'UNKNOWN', 'folder says both WEB and CD'
    if explicit_web:
        return 'WEB', 'folder says WEB'
    if explicit_cd:
        return 'CD', 'folder says CD'
    if source_web:
        return 'WEB', f'source container {source_container}'
    if has_cue and has_log:
        return 'CD', 'CUE file; rip log'
    if has_cue:
        return 'CD', 'CUE file'
    return 'UNKNOWN', 'no reliable CD/WEB evidence'


@dataclass
class Track:
    path: str
    relative_path: str
    title: str
    artist: str
    duration: float
    release: str
    trackno: str = ''
    discno: str = ''
    isrc: str = ''
    mbrec: str = ''
    key: str = ''
    identity_source: str = ''
    ignored_reason: str = ''
    sample_rate: int = 0
    channels: int = 0
    dynamic_range: int | None = None
    dynamic_range_db: float | None = None
    dynamic_range_error: str = ''


@dataclass
class Release:
    rid: str
    folder: str
    name: str
    date: str = ''
    barcode: str = ''
    albumartist: str = ''
    mbrel: str = ''
    source_container: str = ''
    media_type: str = 'UNKNOWN'
    media_evidence: str = ''
    kind: str = 'RELEASE'
    tracks: list[Track] = field(default_factory=list)
    keys: set[str] = field(default_factory=set)
    logchecker_score: float | None = None
    logchecker_logs: list[dict] = field(default_factory=list)
    dynamic_range: int | None = None
    dynamic_range_db: float | None = None
    dynamic_range_measured_tracks: int = 0
    dynamic_range_complete: bool = False
    dynamic_range_attempted: bool = False


class DSU:
    def __init__(self, size: int):
        self.parent = list(range(size))
        self.rank = [0] * size

    def find(self, value: int) -> int:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, left: int, right: int):
        left = self.find(left)
        right = self.find(right)
        if left == right:
            return
        if self.rank[left] < self.rank[right]:
            left, right = right, left
        self.parent[right] = left
        if self.rank[left] == self.rank[right]:
            self.rank[left] += 1


class Model:
    def __init__(self):
        self.releases: dict[str, Release] = {}
        self.forced: set[str] = set()
        self.excluded: set[str] = set()
        self.kept: set[str] = set()
        self.cover = defaultdict(set)
        self.reason: dict[str, str] = {}
        self.lost_keys: set[str] = set()
        self.dr_tiebreak_eligible: set[str] = set()
        self.log_tiebreak_eligible: set[str] = set()
        self.copy_duplicate_rank: dict[str, int] = {}
        self.dr_priority_mode = False

    def scan(self, root: str, progress=lambda a, b: None, dr_priority_mode: bool = False):
        self.dr_priority_mode = bool(dr_priority_mode)
        started = time.perf_counter()
        rootp = Path(root)
        files = [p for p in rootp.rglob('*') if p.is_file() and p.suffix.lower() in AUDIO]
        LOG.event('scan_start', file_count=len(files), root_name=rootp.name)

        groups: dict[str, Release] = {}
        all_tracks: list[Track] = []
        failures = 0

        for i, path in enumerate(files, 1):
            progress(i, len(files))
            try:
                rel = str(path.relative_to(rootp))
            except Exception:
                rel = path.name
            try:
                media = MFile(path, easy=False)
                tags = media.tags or {}
                info = getattr(media, 'info', None)
                duration = float(getattr(info, 'length', 0) or 0)
                sample_rate = int(getattr(info, 'sample_rate', 0) or 0)
                channels = int(getattr(info, 'channels', 0) or 0)
                title = tag(tags, 'title') or path.stem
                artist = tag(tags, 'artist')
                raw_album = tag(tags, 'album')
                albumartist = tag(tags, 'albumartist')
                date = tag(tags, 'date')
                barcode = tag(tags, 'barcode')
                mbrel = tag(tags, 'mbrel')
                disc = tag(tags, 'disc')
                trackno = tag(tags, 'track')
                isrc = tag(tags, 'isrc')
                mbrec = tag(tags, 'mbrec')

                physical_folder = release_folder_for(path, rootp)
                source_container = source_container_for(physical_folder, rootp)
                album = base_album_name(raw_album) or physical_folder.name

                if is_organizational_container(physical_folder):
                    rid_seed = f'loose-track|{path}'
                    rid = hashlib.sha1(rid_seed.encode('utf-8', 'ignore')).hexdigest()[:16]
                    groups[rid] = Release(
                        rid=rid,
                        folder=str(physical_folder),
                        name=title,
                        date=date,
                        barcode=barcode,
                        albumartist=albumartist,
                        mbrel=mbrel,
                        source_container=source_container,
                        media_type='UNKNOWN',
                        media_evidence='loose track in organizational container',
                        kind='LOOSE_TRACK',
                    )
                    LOG.event(
                        'loose_track_detected',
                        release_id=rid,
                        relative_path=rel,
                        organizational_folder=str(physical_folder),
                        title=title,
                    )
                else:
                    rid_seed = str(physical_folder)
                    rid = hashlib.sha1(rid_seed.encode('utf-8', 'ignore')).hexdigest()[:16]
                    if rid not in groups:
                        media_type, media_evidence = classify_media_type(physical_folder, source_container)
                        groups[rid] = Release(
                            rid=rid,
                            folder=str(physical_folder),
                            name=album,
                            date=date,
                            barcode=barcode,
                            albumartist=albumartist,
                            mbrel=mbrel,
                            source_container=source_container,
                            media_type=media_type,
                            media_evidence=media_evidence,
                            kind='RELEASE',
                        )
                    else:
                        existing = groups[rid]
                        conflicts = {}
                        new_album = base_album_name(raw_album)
                        if existing.name and new_album and norm(existing.name) != norm(new_album):
                            conflicts['album'] = {'release': existing.name, 'track': new_album}
                        for field_name, existing_value, new_value in (
                            ('barcode', existing.barcode, barcode),
                            ('musicbrainz_release_id', existing.mbrel, mbrel),
                        ):
                            if existing_value and new_value and norm(existing_value) != norm(new_value):
                                conflicts[field_name] = {'release': existing_value, 'track': new_value}
                        if conflicts:
                            LOG.event(
                                'release_tag_inconsistency',
                                release_id=rid,
                                relative_path=rel,
                                physical_release_folder=str(physical_folder),
                                conflicts=conflicts,
                            )
                        if not existing.date and date:
                            existing.date = date
                        if not existing.barcode and barcode:
                            existing.barcode = barcode
                        if not existing.albumartist and albumartist:
                            existing.albumartist = albumartist
                        if not existing.mbrel and mbrel:
                            existing.mbrel = mbrel

                track = Track(
                    path=str(path),
                    relative_path=rel,
                    title=title,
                    artist=artist,
                    duration=duration,
                    release=rid,
                    trackno=trackno,
                    discno=disc,
                    isrc=isrc,
                    mbrec=mbrec,
                    ignored_reason=ignored_track_reason(
                        title,
                        artist,
                        rel,
                    ),
                    sample_rate=sample_rate,
                    channels=channels,
                )
                groups[rid].tracks.append(track)
                all_tracks.append(track)
                if track.ignored_reason:
                    LOG.event(
                        'track_ignored',
                        relative_path=rel,
                        release_id=rid,
                        release=groups[rid].name,
                        title=title,
                        artist=artist,
                        reason=track.ignored_reason,
                    )
            except Exception as exc:
                failures += 1
                LOG.error('file_read_error', exc, relative_path=rel)

        self.releases = groups

        meaningful_tracks = [track for track in all_tracks if not track.ignored_reason]
        self.resolve_identities(meaningful_tracks)

        for release in self.releases.values():
            release.keys = {
                track.key
                for track in release.tracks
                if track.key and not track.ignored_reason
            }

        logchecker_scored = 0
        logchecker_candidate_count = 0
        for release in self.releases.values():
            if release.kind != 'RELEASE':
                continue
            release.logchecker_score, release.logchecker_logs = score_release_logs(release)
            if release.logchecker_logs:
                logchecker_candidate_count += 1
                logchecker_scored += int(release.logchecker_score is not None)
                LOG.event(
                    'release_logchecker_summary',
                    release_id=release.rid,
                    release=release.name,
                    score=release.logchecker_score,
                    log_count=len(release.logchecker_logs),
                    logs=release.logchecker_logs,
                )

        if self.dr_priority_mode:
            measure_dynamic_range_tracks(all_tracks, progress)
            for release in self.releases.values():
                release.dynamic_range_attempted = True
                finalize_release_dynamic_range(release)
            LOG.event(
                'dynamic_range_priority_scan_complete',
                release_count=len(self.releases),
                measured_release_count=sum(
                    1 for release in self.releases.values()
                    if release.dynamic_range is not None
                ),
            )
        else:
            # DR is expensive, so default analysis measures it only for strict
            # absolute-duplicate candidates where all normal quality/coverage
            # dimensions are already tied. CD candidates additionally require the
            # same Logchecker score. WEB candidates do not require a rip log.
            DR_DUPLICATE_DURATION_TOLERANCE_SEC = 7.0
            duplicate_dr_groups = defaultdict(list)

            def coarse_dr_signature(release: Release):
                if release.kind != 'RELEASE' or release.media_type not in {'CD', 'WEB'}:
                    return None

                if release.media_type == 'CD':
                    if release.logchecker_score is None:
                        return None
                    log_key = round(float(release.logchecker_score), 6)
                else:
                    log_key = None

                track_counts = Counter()
                for track in release.tracks:
                    identity = track.key
                    if not identity:
                        identity = (
                            'raw:' +
                            compact_text(track.artist) + ':' +
                            compact_text(track.title)
                        )
                    track_counts[(identity, track.ignored_reason)] += 1

                return (
                    release.media_type,
                    tuple(sorted(track_counts.items())),
                    len(release.tracks),
                    log_key,
                )

            def dr_pair_compatible(left: Release, right: Release) -> bool:
                left_groups = defaultdict(list)
                right_groups = defaultdict(list)

                for release, target in ((left, left_groups), (right, right_groups)):
                    for track in release.tracks:
                        identity = track.key
                        if not identity:
                            identity = (
                                'raw:' +
                                compact_text(track.artist) + ':' +
                                compact_text(track.title)
                            )
                        target[(identity, track.ignored_reason)].append(
                            float(track.duration or 0.0)
                        )

                if set(left_groups) != set(right_groups):
                    return False

                for key in left_groups:
                    a = sorted(left_groups[key])
                    b = sorted(right_groups[key])
                    if len(a) != len(b):
                        return False
                    for left_duration, right_duration in zip(a, b):
                        if (
                            left_duration > 0.0 and
                            right_duration > 0.0 and
                            abs(left_duration - right_duration) >
                            DR_DUPLICATE_DURATION_TOLERANCE_SEC
                        ):
                            return False
                return True

            for release in self.releases.values():
                signature = coarse_dr_signature(release)
                if signature is not None:
                    duplicate_dr_groups[signature].append(release)

            dr_candidate_ids = set()
            for releases in duplicate_dr_groups.values():
                if len(releases) < 2:
                    continue
                for index, left in enumerate(releases):
                    for right in releases[index + 1:]:
                        if dr_pair_compatible(left, right):
                            dr_candidate_ids.add(left.rid)
                            dr_candidate_ids.add(right.rid)

            dr_candidate_releases = [
                self.releases[rid]
                for rid in sorted(dr_candidate_ids)
            ]
            if dr_candidate_releases:
                candidate_tracks = [
                    track
                    for release in dr_candidate_releases
                    for track in release.tracks
                ]
                measure_dynamic_range_tracks(candidate_tracks, progress)
                for release in dr_candidate_releases:
                    release.dynamic_range_attempted = True
                    finalize_release_dynamic_range(release)

            LOG.event(
                'dynamic_range_lazy_candidates',
                candidate_group_count=sum(
                    1 for releases in duplicate_dr_groups.values()
                    if len(releases) >= 2
                ),
                candidate_release_count=len(dr_candidate_releases),
                measured_release_count=sum(
                    1 for release in dr_candidate_releases
                    if release.dynamic_range is not None
                ),
            )


        for rid, release in sorted(groups.items()):
            try:
                relfolder = str(Path(release.folder).relative_to(rootp))
            except Exception:
                relfolder = Path(release.folder).name
            LOG.event(
                'release_summary',
                release_id=rid,
                release=release.name,
                date=release.date,
                barcode=release.barcode,
                albumartist=release.albumartist,
                musicbrainz_release_id=release.mbrel,
                relative_folder=relfolder,
                source_container=release.source_container,
                media_type=release.media_type,
                media_evidence=release.media_evidence,
                release_kind=release.kind,
                track_count=len(release.tracks),
                meaningful_track_count=sum(1 for track in release.tracks if not track.ignored_reason),
                ignored_track_count=sum(1 for track in release.tracks if track.ignored_reason),
                ignored_reasons=dict(Counter(
                    track.ignored_reason for track in release.tracks if track.ignored_reason
                )),
                distinct_identity_count=len(release.keys),
                logchecker_score=release.logchecker_score,
                logchecker_log_count=len(release.logchecker_logs),
            )

        source_counts = Counter(track.identity_source for track in all_tracks if not track.ignored_reason)
        ignored_counts = Counter(track.ignored_reason for track in all_tracks if track.ignored_reason)
        media_counts = Counter(release.media_type for release in self.releases.values())
        container_counts = Counter(release.source_container for release in self.releases.values())
        LOG.event(
            'scan_parsed',
            release_count=len(groups),
            file_failures=failures,
            identity_sources=dict(source_counts),
            meaningful_track_count=sum(1 for track in all_tracks if not track.ignored_reason),
            ignored_track_count=sum(1 for track in all_tracks if track.ignored_reason),
            ignored_track_reasons=dict(ignored_counts),
            release_media_types=dict(media_counts),
            source_containers=dict(container_counts),
            logchecker_scored_release_count=logchecker_scored,
            logchecker_candidate_release_count=logchecker_candidate_count,
            dynamic_range_complete_release_count=sum(
                1 for release in self.releases.values()
                if release.dynamic_range is not None
            ),
            dynamic_range_attempted_release_count=sum(
                1 for release in self.releases.values()
                if release.dynamic_range_attempted
            ),
            dynamic_range_algorithm=DR_ALGORITHM_REVISION,
            learned_policy_version=LEARNED_POLICY_VERSION,
            dr_priority_mode=self.dr_priority_mode,
            elapsed_sec=round(time.perf_counter() - started, 4),
        )
        self.rebuild()
        self.optimize()
        LOG.event(
            'scan_complete',
            release_count=len(self.releases),
            kept_count=len(self.kept),
            elapsed_sec=round(time.perf_counter() - started, 4),
        )

    def resolve_identities(self, tracks: list[Track]):
        if not tracks:
            return

        dsu = DSU(len(tracks))
        by_mb: dict[str, int] = {}
        by_isrc: dict[str, int] = {}

        cleaned_byte_like = 0
        for idx, track in enumerate(tracks):
            mbid = clean_identifier(track.mbrec)
            isrc = clean_identifier(track.isrc)
            if "b'" in track.isrc or 'b"' in track.isrc:
                cleaned_byte_like += 1
            if mbid:
                if mbid in by_mb:
                    dsu.union(idx, by_mb[mbid])
                else:
                    by_mb[mbid] = idx
            if isrc:
                if isrc in by_isrc:
                    dsu.union(idx, by_isrc[isrc])
                else:
                    by_isrc[isrc] = idx

        components = defaultdict(list)
        for idx in range(len(tracks)):
            components[dsu.find(idx)].append(idx)

        strong_key_by_root: dict[int, str] = {}
        strong_roots_by_title_artist = defaultdict(set)
        conflicts = 0

        for root, indexes in components.items():
            mbids = sorted({clean_identifier(tracks[i].mbrec) for i in indexes if clean_identifier(tracks[i].mbrec)})
            isrcs = sorted({clean_identifier(tracks[i].isrc) for i in indexes if clean_identifier(tracks[i].isrc)})
            if not mbids and not isrcs:
                continue
            if len(mbids) > 1 or len(isrcs) > 1:
                conflicts += 1
                LOG.event(
                    'strong_identity_conflict',
                    musicbrainz_recording_ids=mbids,
                    isrcs=isrcs,
                    relative_paths=[tracks[i].relative_path for i in indexes],
                )
            key = ('mb:' + mbids[0]) if mbids else ('isrc:' + isrcs[0])
            strong_key_by_root[root] = key
            for i in indexes:
                ta = (compact_text(tracks[i].title), compact_text(tracks[i].artist))
                strong_roots_by_title_artist[ta].add(root)

        strong_key_parent = {key: key for key in strong_key_by_root.values()}

        def strong_find(key: str) -> str:
            while strong_key_parent[key] != key:
                strong_key_parent[key] = strong_key_parent[strong_key_parent[key]]
                key = strong_key_parent[key]
            return key

        def strong_rank(key: str):
            return (
                0 if key.startswith('mb:') else
                1 if key.startswith('isrc:') else
                2,
                key,
            )

        def strong_union(left: str, right: str) -> str:
            left = strong_find(left)
            right = strong_find(right)
            if left == right:
                return left
            if strong_rank(right) < strong_rank(left):
                left, right = right, left
            strong_key_parent[right] = left
            return left

        strong_metadata_groups = defaultdict(lambda: defaultdict(list))
        for root in strong_key_by_root:
            for idx in components[root]:
                track = tracks[idx]
                if not track.duration:
                    continue
                base = (
                    compact_text(track.title),
                    compact_text(track.artist),
                    compact_text(version_hint(track.title)),
                )
                if base[0] and base[1]:
                    strong_metadata_groups[base][root].append(track.duration)

        strong_metadata_reconciliations = 0
        for base, root_durations in strong_metadata_groups.items():
            entries = sorted(
                (
                    sum(durations) / len(durations),
                    root,
                    strong_key_by_root[root],
                )
                for root, durations in root_durations.items()
            )
            cluster = []
            cluster_start = None

            def reconcile_cluster(items):
                nonlocal strong_metadata_reconciliations
                if len(items) < 2:
                    return
                canonical = items[0][2]
                for duration, root, key in items[1:]:
                    left = strong_find(canonical)
                    right = strong_find(key)
                    if left == right:
                        continue
                    resolved = strong_union(left, right)
                    strong_metadata_reconciliations += 1
                    LOG.event(
                        'strong_identifier_reconciliation',
                        title=tracks[components[items[0][1]][0]].title,
                        artist=tracks[components[items[0][1]][0]].artist,
                        version_hint=version_hint(tracks[components[items[0][1]][0]].title),
                        left_identity_key=left,
                        right_identity_key=right,
                        resolved_identity_key=resolved,
                        duration_delta_sec=round(abs(items[0][0] - duration), 3),
                    )
                    canonical = resolved

            for entry in entries:
                if cluster_start is None or abs(entry[0] - cluster_start) <= MATCH_DURATION_TOLERANCE:
                    if cluster_start is None:
                        cluster_start = entry[0]
                    cluster.append(entry)
                else:
                    reconcile_cluster(cluster)
                    cluster = [entry]
                    cluster_start = entry[0]
            reconcile_cluster(cluster)

        for root, key in list(strong_key_by_root.items()):
            strong_key_by_root[root] = strong_find(key)

        bridged = 0
        ambiguous = 0
        metadata_only_indexes: list[int] = []

        for idx, track in enumerate(tracks):
            root = dsu.find(idx)
            if root in strong_key_by_root:
                track.key = strong_key_by_root[root]
                if clean_identifier(track.mbrec):
                    track.identity_source = 'musicbrainz_recording_id'
                elif clean_identifier(track.isrc):
                    track.identity_source = 'isrc'
                else:
                    track.identity_source = 'strong_reconciled'
                continue

            ta = (compact_text(track.title), compact_text(track.artist))
            candidates = set()
            for strong_root in strong_roots_by_title_artist.get(ta, set()):
                for other_idx in components[strong_root]:
                    other = tracks[other_idx]
                    if track.duration and other.duration and abs(track.duration - other.duration) <= MATCH_DURATION_TOLERANCE:
                        candidates.add(strong_key_by_root[strong_root])
                        break

            if len(candidates) == 1:
                track.key = next(iter(candidates))
                track.identity_source = 'metadata_bridge'
                bridged += 1
                LOG.event(
                    'metadata_bridge',
                    relative_path=track.relative_path,
                    title=track.title,
                    artist=track.artist,
                    duration_sec=round(track.duration, 3),
                    resolved_identity_key=track.key,
                )
            else:
                metadata_only_indexes.append(idx)
                if len(candidates) > 1:
                    ambiguous += 1
                    LOG.event(
                        'metadata_bridge_ambiguous',
                        relative_path=track.relative_path,
                        title=track.title,
                        artist=track.artist,
                        duration_sec=round(track.duration, 3),
                        candidate_identity_keys=sorted(candidates),
                    )

        metadata_groups = defaultdict(list)
        for idx in metadata_only_indexes:
            track = tracks[idx]
            metadata_groups[(compact_text(track.title), compact_text(track.artist), compact_text(version_hint(track.title)))].append(idx)

        metadata_cluster_count = 0
        for base, indexes in metadata_groups.items():
            indexes.sort(key=lambda i: (tracks[i].duration if tracks[i].duration else float('inf'), tracks[i].relative_path))
            clusters: list[list[int]] = []
            for idx in indexes:
                track = tracks[idx]
                placed = False
                if track.duration:
                    for cluster in clusters:
                        representative = tracks[cluster[0]]
                        if representative.duration and abs(track.duration - representative.duration) <= MATCH_DURATION_TOLERANCE:
                            cluster.append(idx)
                            placed = True
                            break
                elif clusters:
                    clusters[0].append(idx)
                    placed = True
                if not placed:
                    clusters.append([idx])

            for cluster in clusters:
                representative = tracks[cluster[0]]
                payload = f'{base[0]}|{base[1]}|{base[2]}|{round(representative.duration, 3)}'
                key = 'meta:' + hashlib.sha1(payload.encode('utf-8')).hexdigest()[:20]
                metadata_cluster_count += 1
                for idx in cluster:
                    tracks[idx].key = key
                    tracks[idx].identity_source = 'metadata'

        key_parent = {track.key: track.key for track in tracks if track.key}

        def key_rank(key: str):
            return (
                0 if key.startswith('mb:') else
                1 if key.startswith('isrc:') else
                2,
                key,
            )

        def key_find(key: str) -> str:
            if not key:
                return key
            while key_parent[key] != key:
                key_parent[key] = key_parent[key_parent[key]]
                key = key_parent[key]
            return key

        def key_union(left: str, right: str) -> str:
            left = key_find(left)
            right = key_find(right)
            if left == right:
                return left
            if key_rank(right) < key_rank(left):
                left, right = right, left
            key_parent[right] = left
            return left

        def number_from_tag(value: str, default: int) -> int:
            match = re.search(r'\d+', value or '')
            return int(match.group(0)) if match else default

        def release_position_map(release: Release):
            result = {}
            for order, track in enumerate(release.tracks, 1):
                disc = number_from_tag(track.discno, 1)
                number = number_from_tag(track.trackno, order)
                result[(disc, number)] = track
            return result

        alignment_pairs = 0
        alignment_merges = 0
        alignment_strong_reconciliations = 0
        release_values = sorted(self.releases.values(), key=lambda release: release.rid)

        for left_index, left_release in enumerate(release_values):
            if len(left_release.tracks) < 5:
                continue
            for right_release in release_values[left_index + 1:]:
                if len(left_release.tracks) != len(right_release.tracks):
                    continue

                left_map = release_position_map(left_release)
                right_map = release_position_map(right_release)
                positions = sorted(set(left_map) & set(right_map))
                if len(positions) < max(5, int(math.ceil(len(left_release.tracks) * 0.9))):
                    continue

                same_positions = sum(
                    1 for pos in positions
                    if key_find(left_map[pos].key) == key_find(right_map[pos].key)
                )
                aligned_ratio = same_positions / max(1, min(len(left_release.tracks), len(right_release.tracks)))
                if same_positions < 5 or aligned_ratio < 0.70:
                    continue

                alignment_pairs += 1
                LOG.event(
                    'release_alignment_pair',
                    left_release_id=left_release.rid,
                    left_release=left_release.name,
                    right_release_id=right_release.rid,
                    right_release=right_release.name,
                    track_count=len(left_release.tracks),
                    already_matching_positions=same_positions,
                    aligned_ratio=round(aligned_ratio, 4),
                )

                for pos in positions:
                    left_track = left_map[pos]
                    right_track = right_map[pos]
                    left_key = key_find(left_track.key)
                    right_key = key_find(right_track.key)
                    if left_key == right_key:
                        continue
                    if compact_text(left_track.artist) != compact_text(right_track.artist):
                        continue
                    if not left_track.duration or not right_track.duration:
                        continue

                    duration_delta = abs(left_track.duration - right_track.duration)
                    if duration_delta > MATCH_DURATION_TOLERANCE:
                        continue

                    left_title = compact_text(left_track.title)
                    right_title = compact_text(right_track.title)
                    similarity = SequenceMatcher(None, left_title, right_title).ratio() if left_title and right_title else 0.0
                    left_hint = compact_text(version_hint(left_track.title))
                    right_hint = compact_text(version_hint(right_track.title))
                    weak_side = left_track.identity_source == 'metadata' or right_track.identity_source == 'metadata'

                    compatible = (
                        left_title == right_title or
                        (left_hint and left_hint == right_hint and similarity >= 0.60) or
                        (weak_side and similarity >= 0.84)
                    )
                    if not compatible:
                        LOG.event(
                            'release_alignment_unresolved',
                            left_release_id=left_release.rid,
                            right_release_id=right_release.rid,
                            disc=pos[0],
                            track=pos[1],
                            left_title=left_track.title,
                            right_title=right_track.title,
                            duration_delta_sec=round(duration_delta, 3),
                            title_similarity=round(similarity, 4),
                            left_identity_key=left_key,
                            right_identity_key=right_key,
                        )
                        continue

                    strong_conflict = (
                        (left_key.startswith('mb:') or left_key.startswith('isrc:')) and
                        (right_key.startswith('mb:') or right_key.startswith('isrc:'))
                    )
                    merged_key = key_union(left_key, right_key)
                    alignment_merges += 1
                    if strong_conflict:
                        alignment_strong_reconciliations += 1

                    if left_track.identity_source == 'metadata':
                        left_track.identity_source = 'release_alignment_bridge'
                    if right_track.identity_source == 'metadata':
                        right_track.identity_source = 'release_alignment_bridge'

                    LOG.event(
                        'release_alignment_merge',
                        left_release_id=left_release.rid,
                        left_release=left_release.name,
                        right_release_id=right_release.rid,
                        right_release=right_release.name,
                        disc=pos[0],
                        track=pos[1],
                        left_title=left_track.title,
                        right_title=right_track.title,
                        duration_delta_sec=round(duration_delta, 3),
                        title_similarity=round(similarity, 4),
                        left_identity_key=left_key,
                        right_identity_key=right_key,
                        resolved_identity_key=merged_key,
                        strong_identifier_reconciliation=strong_conflict,
                    )

        for track in tracks:
            track.key = key_find(track.key)

        LOG.event(
            'release_alignment_summary',
            candidate_release_pairs=alignment_pairs,
            merged_track_pairs=alignment_merges,
            strong_identifier_reconciliations=alignment_strong_reconciliations,
        )

        for track in tracks:
            LOG.event(
                'track_identity',
                relative_path=track.relative_path,
                release_id=track.release,
                release=self.releases[track.release].name,
                title=track.title,
                artist=track.artist,
                duration_sec=round(track.duration, 3),
                version_hint=version_hint(track.title),
                identity_source=track.identity_source,
                identity_key=track.key,
                isrc=track.isrc,
                musicbrainz_recording_id=track.mbrec,
                disc=track.discno,
                track=track.trackno,
            )

        LOG.event(
            'identity_resolution_summary',
            track_count=len(tracks),
            strong_component_count=len(strong_key_by_root),
            strong_metadata_reconciliation_count=strong_metadata_reconciliations,
            metadata_bridge_count=bridged,
            ambiguous_bridge_count=ambiguous,
            metadata_cluster_count=metadata_cluster_count,
            release_alignment_pair_count=alignment_pairs,
            release_alignment_merge_count=alignment_merges,
            release_alignment_strong_reconciliation_count=alignment_strong_reconciliations,
            strong_conflict_count=conflicts,
            legacy_byte_literal_count=cleaned_byte_like,
        )

    def rebuild(self):
        self.cover = defaultdict(set)
        for release in self.releases.values():
            for key in release.keys:
                self.cover[key].add(release.rid)
        distribution = Counter(len(value) for value in self.cover.values())
        LOG.event(
            'coverage_rebuilt',
            identity_count=len(self.cover),
            provider_count_distribution=dict(sorted(distribution.items())),
        )
        for key, providers in sorted(self.cover.items()):
            LOG.event(
                'coverage_group',
                identity_key=key,
                provider_release_ids=sorted(providers),
                provider_count=len(providers),
            )

        self.log_tiebreak_eligible = set()
        log_groups = defaultdict(list)
        for rid, release in self.releases.items():
            if release.media_type == 'CD' and release.logchecker_score is not None:
                signature = (
                    frozenset(release.keys),
                    len(release.tracks),
                    raw_track_signature(release),
                )
                log_groups[signature].append(rid)

        self.log_tiebreak_eligible = {
            rid
            for release_ids in log_groups.values()
            if len(release_ids) >= 2
            for rid in release_ids
        }

        self.copy_duplicate_rank = {}
        copy_groups = defaultdict(list)
        for rid, release in self.releases.items():
            fingerprints = release_log_fingerprints(release)
            if not fingerprints:
                continue
            signature = (
                fingerprints,
                exact_audio_duplicate_signature(release),
            )
            copy_groups[signature].append(rid)

        for release_ids in copy_groups.values():
            if len(release_ids) < 2:
                continue
            ordered = sorted(
                release_ids,
                key=lambda rid: duplicate_copy_rank(self.releases[rid]),
            )
            for rank, rid in enumerate(ordered):
                self.copy_duplicate_rank[rid] = rank

        equivalent = defaultdict(list)
        for rid, release in self.releases.items():
            if release.dynamic_range is None:
                continue
            if release.media_type == 'CD':
                if release.logchecker_score is None:
                    continue
                log_key = round(float(release.logchecker_score), 6)
            elif release.media_type == 'WEB':
                log_key = None
            else:
                continue

            signature = (
                release.media_type,
                frozenset(release.keys),
                len(release.tracks),
                raw_track_signature(release),
                log_key,
            )
            equivalent[signature].append(rid)

        self.dr_tiebreak_eligible = {
            rid
            for release_ids in equivalent.values()
            if len(release_ids) >= 2
            for rid in release_ids
        }

        LOG.event(
            'learned_policy_groups',
            policy_version=LEARNED_POLICY_VERSION,
            log_tiebreak_release_count=len(self.log_tiebreak_eligible),
            copied_duplicate_release_count=len(self.copy_duplicate_rank),
            dr_tiebreak_release_count=len(self.dr_tiebreak_eligible),
            dr_priority_mode=self.dr_priority_mode,
        )

    @staticmethod
    def media_rank(media_type: str) -> int:
        return 0 if media_type == 'CD' else 1

    def log_tiebreak_value(self, rid: str) -> float:
        if rid not in self.log_tiebreak_eligible:
            return 0.0
        score = self.releases[rid].logchecker_score
        return float(score) if score is not None else 0.0

    def dr_tiebreak_value(self, rid: str) -> float:
        if rid not in self.dr_tiebreak_eligible:
            return 0.0
        release = self.releases[rid]
        if release.dynamic_range_db is not None:
            return float(release.dynamic_range_db)
        if release.dynamic_range is not None:
            return float(release.dynamic_range)
        return 0.0

    def dr_priority_value(self, rid: str) -> float:
        release = self.releases[rid]
        if release.dynamic_range_db is not None:
            return float(release.dynamic_range_db)
        if release.dynamic_range is not None:
            return float(release.dynamic_range)
        return 0.0

    def copied_duplicate_penalty(self, rid: str) -> int:
        return int(self.copy_duplicate_rank.get(rid, 0))

    def objective(self, selected: set[str]):
        file_count = sum(len(self.releases[rid].tracks) for rid in selected)
        media_penalty = sum(self.media_rank(self.releases[rid].media_type) for rid in selected)
        log_score_total = sum(self.log_tiebreak_value(rid) for rid in selected)
        copy_penalty = sum(self.copied_duplicate_penalty(rid) for rid in selected)
        dr_tiebreak_total = sum(self.dr_tiebreak_value(rid) for rid in selected)
        dr_priority_total = sum(self.dr_priority_value(rid) for rid in selected)
        stable = tuple(sorted(
            (self.releases[rid].date, self.releases[rid].name, rid)
            for rid in selected
        ))

        if self.dr_priority_mode:
            return (
                len(selected),
                -dr_priority_total,
                file_count,
                media_penalty,
                -log_score_total,
                copy_penalty,
                stable,
            )

        return (
            len(selected),
            file_count,
            media_penalty,
            -log_score_total,
            copy_penalty,
            -dr_tiebreak_total,
            stable,
        )

    def optimize(self):
        started = time.perf_counter()
        avail = set(self.releases) - self.excluded
        forced = self.forced & avail
        universe = set().union(*(release.keys for release in self.releases.values())) if self.releases else set()
        coverable = {key for key in universe if self.cover[key] & avail}
        self.lost_keys = universe - coverable
        covered = set().union(*(self.releases[r].keys for r in forced)) if forced else set()
        covered &= coverable
        chosen = set(forced)
        remaining = coverable - covered

        LOG.event(
            'optimize_start',
            release_count=len(self.releases),
            available_count=len(avail),
            forced=sorted(forced),
            excluded=sorted(self.excluded),
            identity_universe=len(universe),
            coverable_identity_count=len(coverable),
            lost_identity_count=len(self.lost_keys),
            lost_identity_keys=sorted(self.lost_keys),
            already_covered=len(covered),
        )

        step = 0
        while remaining:
            candidates = [rid for rid in avail - chosen if self.releases[rid].keys & remaining]
            if not candidates:
                LOG.event('optimizer_uncovered', remaining_identity_keys=sorted(remaining))
                break

            def greedy_key(rid: str):
                release = self.releases[rid]
                if self.dr_priority_mode:
                    return (
                        -len(release.keys & remaining),
                        -self.dr_priority_value(rid),
                        len(release.tracks),
                        self.media_rank(release.media_type),
                        -self.log_tiebreak_value(rid),
                        self.copied_duplicate_penalty(rid),
                        release.date,
                        release.name,
                        rid,
                    )
                return (
                    -len(release.keys & remaining),
                    len(release.tracks),
                    self.media_rank(release.media_type),
                    -self.log_tiebreak_value(rid),
                    self.copied_duplicate_penalty(rid),
                    -self.dr_tiebreak_value(rid),
                    release.date,
                    release.name,
                    rid,
                )

            candidates.sort(key=greedy_key)
            rid = candidates[0]
            new_keys = self.releases[rid].keys & remaining
            chosen.add(rid)
            remaining -= self.releases[rid].keys
            step += 1
            LOG.event(
                'greedy_choice',
                step=step,
                chosen_release_id=rid,
                chosen_release=self.releases[rid].name,
                newly_covered=len(new_keys),
                chosen_media_type=self.releases[rid].media_type,
                chosen_media_penalty=self.media_rank(self.releases[rid].media_type),
                chosen_file_count=len(self.releases[rid].tracks),
                chosen_logchecker_score=self.releases[rid].logchecker_score,
                chosen_dynamic_range=self.releases[rid].dynamic_range,
                chosen_dynamic_range_tiebreak=self.dr_tiebreak_value(rid),
                remaining_after=len(remaining),
                top_candidates=[
                    {
                        'release_id': cand,
                        'name': self.releases[cand].name,
                        'new_coverage': len(self.releases[cand].keys & (remaining | new_keys)),
                        'media_type': self.releases[cand].media_type,
                        'media_penalty': self.media_rank(self.releases[cand].media_type),
                        'file_count': len(self.releases[cand].tracks),
                        'logchecker_score': self.releases[cand].logchecker_score,
                        'dynamic_range': self.releases[cand].dynamic_range,
                        'dynamic_range_tiebreak': self.dr_tiebreak_value(cand),
                    }
                    for cand in candidates[:12]
                ],
            )

        changed = True
        while changed:
            changed = False
            ordered = sorted(
                chosen - forced,
                key=lambda rid: (
                    -self.dr_priority_value(rid) if self.dr_priority_mode else 0.0,
                    -len(self.releases[rid].tracks),
                    -self.media_rank(self.releases[rid].media_type),
                    self.log_tiebreak_value(rid),
                    -self.copied_duplicate_penalty(rid),
                    self.dr_tiebreak_value(rid),
                    self.releases[rid].date,
                    self.releases[rid].name,
                ),
            )
            for rid in ordered:
                others = chosen - {rid}
                coverage = set().union(*(self.releases[x].keys for x in others)) if others else set()
                if coverable <= coverage:
                    chosen.remove(rid)
                    changed = True
                    LOG.event(
                        'redundancy_elimination',
                        removed_release_id=rid,
                        removed_release=self.releases[rid].name,
                        removed_file_count=len(self.releases[rid].tracks),
                        remaining_selected=len(chosen),
                    )

        best = set(chosen)
        best_obj = self.objective(best)

        exact_selected = set(forced)
        exact_covered = set().union(*(self.releases[rid].keys for rid in exact_selected)) if exact_selected else set()
        exact_covered &= coverable
        mandatory_added = 0

        while True:
            missing = coverable - exact_covered
            mandatory = set()
            for key in missing:
                providers = self.cover[key] & avail
                if len(providers) == 1:
                    mandatory.update(providers)
            mandatory -= exact_selected
            if not mandatory:
                break
            exact_selected.update(mandatory)
            mandatory_added += len(mandatory)
            exact_covered |= set().union(*(self.releases[rid].keys for rid in mandatory))

        residual_keys = coverable - exact_covered
        candidate_releases = set().union(*(self.cover[key] & avail for key in residual_keys)) if residual_keys else set()
        candidate_releases -= exact_selected

        component_parent = {rid: rid for rid in candidate_releases}

        def component_find(rid: str) -> str:
            while component_parent[rid] != rid:
                component_parent[rid] = component_parent[component_parent[rid]]
                rid = component_parent[rid]
            return rid

        def component_union(left: str, right: str):
            left = component_find(left)
            right = component_find(right)
            if left != right:
                component_parent[right] = left

        for key in residual_keys:
            providers = sorted((self.cover[key] & candidate_releases))
            for rid in providers[1:]:
                component_union(providers[0], rid)

        components_map = defaultdict(set)
        for rid in candidate_releases:
            components_map[component_find(rid)].add(rid)

        components_list = sorted(
            components_map.values(),
            key=lambda component: (-len(component), sorted(component)),
        )

        exact_stats = {
            'components': len(components_list),
            'mandatory_release_count': len(exact_selected),
            'mandatory_added': mandatory_added,
            'nodes': 0,
            'solutions': 0,
            'pruned_size': 0,
            'pruned_lower_bound': 0,
            'pruned_memo': 0,
        }

        LOG.event(
            'exact_search_start',
            total_release_count=len(avail),
            mandatory_release_count=len(exact_selected),
            residual_identity_count=len(residual_keys),
            residual_candidate_count=len(candidate_releases),
            component_count=len(components_list),
            component_sizes=sorted((len(component) for component in components_list), reverse=True),
            seed_release_count=best_obj[0],
            seed_media_penalty=best_obj[1],
            seed_file_count=best_obj[2],
        )

        for component_index, component in enumerate(components_list, 1):
            component_keys = {
                key for key in residual_keys
                if self.cover[key] & component
            }

            seed = best & component
            seed_coverage = set().union(*(self.releases[rid].keys for rid in seed)) if seed else set()
            if not component_keys <= seed_coverage:
                seed = set()
                uncovered = set(component_keys)
                while uncovered:
                    candidates = sorted(
                        (rid for rid in component if self.releases[rid].keys & uncovered),
                        key=lambda rid: (
                            -len(self.releases[rid].keys & uncovered),
                            self.media_rank(self.releases[rid].media_type),
                            len(self.releases[rid].tracks),
                            -(float(self.releases[rid].logchecker_score) if self.releases[rid].logchecker_score is not None else 0.0),
                            self.releases[rid].date,
                            self.releases[rid].name,
                            rid,
                        ),
                    )
                    if not candidates:
                        break
                    rid = candidates[0]
                    seed.add(rid)
                    uncovered -= self.releases[rid].keys

            component_best = set(seed)
            component_best_obj = self.objective(component_best)
            component_nodes = 0
            component_solutions = 0
            memo = {}

            LOG.event(
                'exact_component_start',
                component_index=component_index,
                release_count=len(component),
                identity_count=len(component_keys),
                seed_release_count=component_best_obj[0],
                seed_media_penalty=component_best_obj[1],
                seed_file_count=component_best_obj[2],
                release_ids=sorted(component),
            )

            def component_dfs(selected: set[str], coverage: set[str]):
                nonlocal component_best, component_best_obj, component_nodes, component_solutions
                component_nodes += 1
                exact_stats['nodes'] += 1

                missing = component_keys - coverage
                if not missing:
                    component_solutions += 1
                    exact_stats['solutions'] += 1
                    objective = self.objective(selected)
                    if objective < component_best_obj:
                        component_best = set(selected)
                        component_best_obj = objective
                    return

                if len(selected) >= component_best_obj[0]:
                    exact_stats['pruned_size'] += 1
                    return

                state = frozenset(missing)
                partial = (
                    len(selected),
                    sum(self.media_rank(self.releases[rid].media_type) for rid in selected),
                    sum(len(self.releases[rid].tracks) for rid in selected),
                    -sum(
                        float(self.releases[rid].logchecker_score)
                        for rid in selected
                        if self.releases[rid].logchecker_score is not None
                    ),
                )
                previous = memo.get(state)
                if previous is not None and previous <= partial:
                    exact_stats['pruned_memo'] += 1
                    return
                memo[state] = partial

                available_candidates = component - selected
                max_new = max(
                    (len(self.releases[rid].keys & missing) for rid in available_candidates),
                    default=0,
                )
                if max_new <= 0:
                    return

                lower_bound = math.ceil(len(missing) / max_new)
                if len(selected) + lower_bound > component_best_obj[0]:
                    exact_stats['pruned_lower_bound'] += 1
                    return

                key = min(
                    missing,
                    key=lambda item: len((self.cover[item] & component) - selected),
                )
                providers = sorted(
                    (self.cover[key] & component) - selected,
                    key=lambda rid: (
                        -len(self.releases[rid].keys & missing),
                        self.media_rank(self.releases[rid].media_type),
                        len(self.releases[rid].tracks),
                        -(float(self.releases[rid].logchecker_score) if self.releases[rid].logchecker_score is not None else 0.0),
                        self.releases[rid].date,
                        self.releases[rid].name,
                        rid,
                    ),
                )
                for rid in providers:
                    component_dfs(
                        selected | {rid},
                        coverage | self.releases[rid].keys,
                    )

            component_dfs(set(), set())
            exact_selected.update(component_best)

            LOG.event(
                'exact_component_complete',
                component_index=component_index,
                release_count=len(component),
                identity_count=len(component_keys),
                best_release_count=component_best_obj[0],
                best_media_penalty=component_best_obj[1],
                best_file_count=component_best_obj[2],
                selected_release_ids=sorted(component_best),
                nodes=component_nodes,
                solutions=component_solutions,
            )

        exact_coverage = set().union(*(self.releases[rid].keys for rid in exact_selected)) if exact_selected else set()
        if coverable <= exact_coverage:
            exact_obj = self.objective(exact_selected)
            if exact_obj < best_obj:
                best = exact_selected
                best_obj = exact_obj

        LOG.event(
            'exact_search_complete',
            best_release_count=best_obj[0],
            best_objective=list(best_obj[:-1]),
            dr_priority_mode=self.dr_priority_mode,
            lost_identity_count=len(self.lost_keys),
            **exact_stats,
        )

        self.kept = best
        self.reason = {}
        kept_counts = Counter(key for rid in self.kept for key in self.releases[rid].keys)

        for rid, release in self.releases.items():
            if rid in self.excluded:
                lost_here = len(release.keys & self.lost_keys)
                self.reason[rid] = (
                    f'Excluded by user: {lost_here} track version(s) now have no remaining provider'
                    if lost_here else
                    'Excluded by user'
                )
            elif rid in self.kept:
                unique = [key for key in release.keys if kept_counts[key] == 1]
                if unique:
                    self.reason[rid] = f'Kept: supplies {len(unique)} required track version(s) not supplied by another kept release'
                else:
                    self.reason[rid] = 'Kept: part of the smallest coverage plan found'
            elif not release.keys:
                ignored_count = sum(1 for track in release.tracks if track.ignored_reason)
                if ignored_count and ignored_count == len(release.tracks):
                    self.reason[rid] = 'Irrelevant: contains only ignored remix/live recordings'
                else:
                    self.reason[rid] = 'Irrelevant: contributes no meaningful comparable tracks'
            else:
                equivalent_higher_dr = [
                    other
                    for other in self.kept
                    if (
                        rid in self.dr_tiebreak_eligible and
                        other in self.dr_tiebreak_eligible and
                        self.releases[other].media_type == release.media_type and
                        self.releases[other].keys == release.keys and
                        len(self.releases[other].tracks) == len(release.tracks) and
                        (
                            release.media_type != 'CD' or
                            (
                                self.releases[other].logchecker_score is not None and
                                release.logchecker_score is not None and
                                abs(
                                    float(self.releases[other].logchecker_score) -
                                    float(release.logchecker_score)
                                ) < 1e-9
                            )
                        ) and
                        self.dr_tiebreak_value(other) > self.dr_tiebreak_value(rid)
                    )
                ]
                cd_covered = bool(release.keys) and all(
                    any(
                        provider in self.kept and self.releases[provider].media_type == 'CD'
                        for provider in self.cover[key]
                    )
                    for key in release.keys
                )
                if equivalent_higher_dr:
                    winner = max(equivalent_higher_dr, key=self.dr_tiebreak_value)
                    self.reason[rid] = (
                        'Redundant: an otherwise-identical '
                        f'{release.media_type} release has higher dynamic range '
                        f'(DR{self.releases[winner].dynamic_range} vs DR{release.dynamic_range})'
                    )
                elif release.media_type == 'WEB' and cd_covered:
                    self.reason[rid] = 'Redundant in current plan: equivalent content is supplied by preferred CD release(s)'
                else:
                    self.reason[rid] = 'Redundant in current plan: all track versions are supplied by kept releases'

        status_counts = Counter(
            'EXCLUDED' if rid in self.excluded else
            'FORCED' if rid in self.forced else
            'KEEP' if rid in self.kept else
            'IRRELEVANT' if not release.keys else
            'REDUNDANT'
            for rid, release in self.releases.items()
        )

        LOG.event(
            'optimize_complete',
            kept_release_ids=sorted(self.kept),
            kept_count=len(self.kept),
            lost_identity_count=len(self.lost_keys),
            lost_identity_keys=sorted(self.lost_keys),
            kept_media_penalty=sum(self.media_rank(self.releases[rid].media_type) for rid in self.kept),
            kept_media_types=dict(Counter(self.releases[rid].media_type for rid in self.kept)),
            kept_file_count=sum(len(self.releases[rid].tracks) for rid in self.kept),
            status_counts=dict(status_counts),
            elapsed_sec=round(time.perf_counter() - started, 4),
            reasons={rid: self.reason[rid] for rid in sorted(self.reason)},
        )

    def impact(self, rid: str):
        if rid not in self.kept:
            return []
        others = self.kept - {rid}
        coverage = set().union(*(self.releases[x].keys for x in others)) if others else set()
        return sorted(self.releases[rid].keys - coverage)

    def exclusion_losses(self, rid: str):
        if rid not in self.releases:
            return []
        remaining = set(self.releases) - self.excluded - {rid}
        return sorted(
            key for key in self.releases[rid].keys
            if not (self.cover[key] & remaining)
        )


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.m = Model()
        self.dr_priority_var = tk.BooleanVar(value=False)
        LOG.event('ui_start')
        self.title(f'{APP} {VERSION} - {STATUS}')
        self.geometry('1420x860')
        self.minsize(1050, 650)
        self.configure(bg='#16191d')
        self.style()
        self.build()
        self.load_settings()
        self.protocol('WM_DELETE_WINDOW', self.on_close)

    def style(self):
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('.', background='#1d2126', foreground='#e7e9ec', fieldbackground='#242a30', font=('Segoe UI', 10))
        style.configure('TFrame', background='#16191d')
        style.configure('TLabel', background='#16191d')
        style.configure('TButton', padding=7)
        style.configure('Treeview', rowheight=27, background='#1d2126', fieldbackground='#1d2126', foreground='#e7e9ec')
        style.configure('Treeview.Heading', background='#2a3037', foreground='#fff', font=('Segoe UI Semibold', 10))
        style.map('Treeview', background=[('selected', '#34495e')])

    def build(self):
        top = ttk.Frame(self)
        top.pack(fill='x', padx=12, pady=10)
        ttk.Label(top, text=APP, font=('Segoe UI Semibold', 18)).pack(side='left')
        ttk.Label(top, text=f'{VERSION} - {STATUS}').pack(side='left', padx=12)
        ttk.Button(top, text='Analyze folder', command=self.choose).pack(side='right')
        self.dr_priority_check = ttk.Checkbutton(
            top,
            text='DR priority mode',
            variable=self.dr_priority_var,
            command=self.on_dr_priority_toggle,
        )
        self.dr_priority_check.pack(side='right', padx=8)
        ttk.Button(top, text='Reset decisions', command=self.reset).pack(side='right', padx=6)
        ttk.Button(top, text='Open logs', command=self.open_logs).pack(side='right')

        self.q = tk.StringVar()
        entry = ttk.Entry(top, textvariable=self.q, width=34)
        entry.pack(side='right', padx=8)
        entry.bind('<KeyRelease>', lambda _: self.refresh())

        pane = ttk.Panedwindow(self, orient='horizontal')
        pane.pack(fill='both', expand=True, padx=12, pady=(0, 12))
        left = ttk.Frame(pane)
        right = ttk.Frame(pane)
        pane.add(left, weight=3)
        pane.add(right, weight=2)

        columns = ('status', 'date', 'release', 'media', 'source', 'tracks', 'unique', 'dr', 'log_score', 'barcode')
        self.tree = ttk.Treeview(left, columns=columns, show='headings')
        for column, width in (
            ('status', 100), ('date', 95), ('release', 310), ('media', 75), ('source', 95),
            ('tracks', 65), ('unique', 70), ('dr', 60), ('log_score', 85), ('barcode', 135)
        ):
            self.tree.heading(column, text=column.title())
            self.tree.column(column, width=width, anchor='w')
        self.tree.pack(fill='both', expand=True)
        self.tree.bind('<<TreeviewSelect>>', self.select)

        bar = ttk.Frame(left)
        bar.pack(fill='x', pady=(8, 0))
        ttk.Button(bar, text='Exclude release', command=self.exclude).pack(side='left')
        ttk.Button(bar, text='Force keep', command=self.force).pack(side='left', padx=6)
        ttk.Button(bar, text='Clear decision', command=self.clear_decision).pack(side='left')
        self.summary = ttk.Label(bar, text='')
        self.summary.pack(side='right')

        self.detail = tk.Text(
            right,
            bg='#111418',
            fg='#e7e9ec',
            insertbackground='white',
            relief='flat',
            font=('Consolas', 10),
            wrap='word',
            padx=12,
            pady=12,
        )
        self.detail.pack(fill='both', expand=True)

        self.canvas = tk.Canvas(right, height=240, bg='#111418', highlightthickness=0)
        self.canvas.pack(fill='x', pady=(8, 0))
        self.canvas.bind('<Button-1>', self.canvas_click)
        self.nodes = []

        self.status = ttk.Label(self, text='Ready')
        self.status.pack(fill='x', padx=12, pady=(0, 8))

    def choose(self):
        dialog = {'title': 'Select music collection'}
        if getattr(self, 'last', '') and Path(self.last).is_dir():
            dialog['initialdir'] = self.last
        path = filedialog.askdirectory(**dialog)
        if not path:
            return
        self.analyze_path(path)

    def analyze_path(self, path: str):
        self.rootpath = str(path)
        self.last = self.rootpath
        LOG.event(
            'user_analyze_folder',
            root_name=Path(path).name,
            dr_priority_mode=bool(self.dr_priority_var.get()),
            policy_version=LEARNED_POLICY_VERSION,
        )
        self.status.config(
            text=(
                'Scanning + full DR priority analysis...'
                if self.dr_priority_var.get()
                else 'Scanning...'
            )
        )

        def run():
            priority = bool(self.dr_priority_var.get())
            self.m.scan(
                self.rootpath,
                lambda i, n: self.after(
                    0,
                    lambda i=i, n=n, priority=priority: self.status.config(
                        text=(
                            f'Scanning / DR priority {i:,}/{n:,} files...'
                            if priority else
                            f'Scanning {i:,}/{n:,} files...'
                        )
                    ),
                ),
                dr_priority_mode=priority,
            )
            self.after(0, self.done)

        threading.Thread(target=run, daemon=True).start()

    def on_dr_priority_toggle(self):
        self.save_settings()
        enabled = bool(self.dr_priority_var.get())
        LOG.event('dr_priority_mode_changed', enabled=enabled)
        if getattr(self, 'rootpath', '') and Path(self.rootpath).is_dir():
            self.analyze_path(self.rootpath)
        else:
            self.status.config(
                text=(
                    'DR priority mode enabled - next analysis will measure every release.'
                    if enabled else
                    'DR priority mode disabled - DR returns to lazy duplicate-only measurement.'
                )
            )

    def done(self):
        self.refresh()
        self.status.config(
            text=(
                f'Analysis complete - {len(self.m.releases):,} releases - '
                f'policy {LEARNED_POLICY_VERSION} - '
                f'DR priority: {"ON" if self.dr_priority_var.get() else "OFF"} - '
                f'log: {LOG.path.name}'
            )
        )
        self.save_settings()
        LOG.event('ui_analysis_complete', release_count=len(self.m.releases), kept_count=len(self.m.kept))

    def status_of(self, rid: str):
        if rid in self.m.excluded:
            return 'EXCLUDED'
        if rid in self.m.forced:
            return 'FORCED'
        if rid in self.m.kept:
            return 'KEEP'
        if not self.m.releases[rid].keys:
            return 'IRRELEVANT'
        return 'REDUNDANT'

    def refresh(self):
        selection = self.tree.selection()
        old = selection[0] if selection else None
        self.tree.delete(*self.tree.get_children())
        query = norm(self.q.get())
        kept_counts = Counter(key for rid in self.m.kept for key in self.m.releases[rid].keys)

        for rid, release in sorted(self.m.releases.items(), key=lambda item: (item[1].date, item[1].name, item[0])):
            haystack = norm(
                release.name + ' ' + release.barcode + ' ' + release.media_type + ' ' +
                release.source_container + ' ' +
                ' '.join(track.title + ' ' + track.artist for track in release.tracks)
            )
            if query and query not in haystack:
                continue
            unique = sum(1 for key in release.keys if rid in self.m.kept and kept_counts[key] == 1)
            self.tree.insert(
                '',
                'end',
                iid=rid,
                values=(
                    self.status_of(rid), release.date, release.name, release.media_type,
                    release.source_container, len(release.tracks), unique,
                    (f'DR{release.dynamic_range}' if release.dynamic_range is not None else '-'),
                    (f'{release.logchecker_score:g}' if release.logchecker_score is not None else '-'),
                    release.barcode
                ),
            )

        kept_files = sum(len(self.m.releases[rid].tracks) for rid in self.m.kept)
        kept_media = Counter(self.m.releases[rid].media_type for rid in self.m.kept)
        ignored_tracks = sum(
            1
            for release in self.m.releases.values()
            for track in release.tracks
            if track.ignored_reason
        )
        self.summary.config(
            text=(
                f'{len(self.m.kept)} kept / {len(self.m.releases)} releases - {kept_files} files - '
                f'CD:{kept_media.get("CD", 0)} WEB:{kept_media.get("WEB", 0)} '
                f'UNKNOWN:{kept_media.get("UNKNOWN", 0)} - '
                f'IGNORED:{ignored_tracks} - LOST:{len(self.m.lost_keys)}'
            )
        )
        if old and self.tree.exists(old):
            self.tree.selection_set(old)
            self.select()

    def selected(self):
        selection = self.tree.selection()
        return selection[0] if selection else None

    def exclude(self):
        rid = self.selected()
        if not rid:
            return
        before = self.m.impact(rid)
        losses = self.m.exclusion_losses(rid)
        if losses:
            prompt = (
                f'Excluding this release will make {len(losses)} track version(s) unavailable because no other release supplies them.\n\n'
                'Exclude it anyway and recalculate the remaining collection?'
            )
        elif before:
            prompt = (
                f'This release currently supplies {len(before)} track version(s) not supplied by another kept release, '
                'but alternatives exist elsewhere in the collection.\n\n'
                'Exclude it and recalculate replacements?'
            )
        else:
            prompt = 'Exclude this release and recalculate the collection?'
        if not messagebox.askyesno('Recalculate', prompt):
            return
        LOG.event(
            'user_decision',
            action='exclude',
            release_id=rid,
            release=self.m.releases[rid].name,
            unique_in_current_plan=len(before),
            permanently_lost_if_excluded=len(losses),
        )
        self.m.excluded.add(rid)
        self.m.forced.discard(rid)
        self.m.optimize()
        self.refresh()

    def force(self):
        rid = self.selected()
        if not rid:
            return
        LOG.event('user_decision', action='force_keep', release_id=rid, release=self.m.releases[rid].name)
        self.m.forced.add(rid)
        self.m.excluded.discard(rid)
        self.m.optimize()
        self.refresh()

    def clear_decision(self):
        rid = self.selected()
        if not rid:
            return
        LOG.event('user_decision', action='clear', release_id=rid, release=self.m.releases[rid].name)
        self.m.forced.discard(rid)
        self.m.excluded.discard(rid)
        self.m.optimize()
        self.refresh()

    def reset(self):
        LOG.event('user_decision', action='reset_all')
        self.m.forced.clear()
        self.m.excluded.clear()
        self.m.optimize()
        self.refresh()

    def select(self, *_):
        rid = self.selected()
        if not rid:
            return
        release = self.m.releases[rid]
        impact = set(self.m.impact(rid))
        self.detail.delete('1.0', 'end')
        self.detail.insert(
            'end',
            f'{release.date} - {release.name}\n'
            f'{release.albumartist}\n'
            f'Barcode: {release.barcode or "-"}\n'
            f'Source container: {release.source_container or "-"}\n'
            f'Media: {release.media_type} ({release.media_evidence})\n'
            f'Dynamic range: {dynamic_range_display(release)}\n'
            f'Logchecker score: {logchecker_display(release)}\n'
            f'Kind: {release.kind}\n'
            f'Folder: {release.folder}\n\n'
            f'{self.status_of(rid)}\n'
            f'{self.m.reason.get(rid, "")}\n\n'
            'TRACKS\n',
        )
        for i, track in enumerate(release.tracks, 1):
            if track.ignored_reason:
                self.detail.insert(
                    'end',
                    f'{i:02d}. {track.artist} - {track.title}  ({track.duration:.0f}s)  '
                    f'[{("DR" + str(track.dynamic_range)) if track.dynamic_range is not None else "DR?"}]  '
                    f'[IGNORED: {track.ignored_reason}]\n',
                )
                continue

            if track.key in self.m.lost_keys:
                flag = '  [NO REMAINING PROVIDER]'
            elif track.key in impact:
                flag = '  [IRREPLACEABLE IN CURRENT PLAN]'
            else:
                flag = ''
            alternatives = len((self.m.cover[track.key] - {rid}) - self.m.excluded)
            self.detail.insert(
                'end',
                f'{i:02d}. {track.artist} - {track.title}  ({track.duration:.0f}s)  '
                f'[{("DR" + str(track.dynamic_range)) if track.dynamic_range is not None else "DR?"}]  '
                f'alternatives:{alternatives}  identity:{track.identity_source}{flag}\n',
            )
        self.draw_graph(rid)

    def draw_graph(self, rid: str):
        canvas = self.canvas
        canvas.delete('all')
        self.nodes = []
        width = max(canvas.winfo_width(), 500)
        height = 240
        cx = width / 2
        cy = height / 2
        release = self.m.releases[rid]
        related = Counter()
        for key in release.keys:
            for other in self.m.cover[key] - {rid}:
                related[other] += 1
        rel = related.most_common(10)

        canvas.create_oval(cx - 55, cy - 28, cx + 55, cy + 28, fill='#34495e', outline='')
        canvas.create_text(cx, cy, text=release.name[:18], fill='white', width=100)

        for j, (other, count) in enumerate(rel):
            angle = 2 * math.pi * j / max(1, len(rel))
            x0 = cx + 180 * math.cos(angle)
            y0 = cy + 82 * math.sin(angle)
            canvas.create_line(cx, cy, x0, y0, fill='#59636e', width=max(1, min(6, count)))
            canvas.create_oval(x0 - 48, y0 - 22, x0 + 48, y0 + 22, fill='#242a30', outline='#59636e')
            canvas.create_text(x0, y0, text=self.m.releases[other].name[:15], fill='#e7e9ec', width=90)
            self.nodes.append((x0 - 48, y0 - 22, x0 + 48, y0 + 22, other))

    def canvas_click(self, event):
        for x1, y1, x2, y2, rid in self.nodes:
            if x1 <= event.x <= x2 and y1 <= event.y <= y2 and self.tree.exists(rid):
                self.tree.selection_set(rid)
                self.tree.see(rid)
                self.select()
                break

    def open_logs(self):
        try:
            folder = str(DATA / 'logs')
            if os.name == 'nt':
                os.startfile(folder)
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', folder])
            else:
                subprocess.Popen(['xdg-open', folder])
            LOG.event('open_logs_folder')
        except Exception as exc:
            LOG.error('open_logs_error', exc)
            messagebox.showerror(APP, f'Could not open logs folder:\n{exc}')

    def report_callback_exception(self, exc, value, tb):
        LOG.event(
            'ui_callback_error',
            error_type=getattr(exc, '__name__', str(exc)),
            error=str(value),
            traceback=''.join(traceback.format_exception(exc, value, tb)),
        )
        messagebox.showerror(APP, f'Unexpected error: {value}\n\nThe diagnostic log contains the traceback.')

    def on_close(self):
        self.save_settings()
        LOG.close()
        self.destroy()

    def load_settings(self):
        try:
            data = json.loads(SETTINGS.read_text('utf-8'))
            self.last = data.get('last_folder', '')
            self.dr_priority_var.set(bool(data.get('dr_priority_mode', False)))
        except Exception:
            self.last = ''
            self.dr_priority_var.set(False)

    def save_settings(self):
        try:
            SETTINGS.write_text(
                json.dumps(
                    {
                        'last_folder': getattr(self, 'rootpath', getattr(self, 'last', '')),
                        'dr_priority_mode': bool(self.dr_priority_var.get()),
                    },
                    indent=2,
                ),
                encoding='utf-8',
            )
        except Exception:
            pass


def _fatal_hook(exc_type, exc, tb):
    LOG.event(
        'fatal_error',
        error_type=getattr(exc_type, '__name__', str(exc_type)),
        error=str(exc),
        traceback=''.join(traceback.format_exception(exc_type, exc, tb)),
    )
    sys.__excepthook__(exc_type, exc, tb)


sys.excepthook = _fatal_hook

if hasattr(threading, 'excepthook'):
    def _thread_hook(args):
        LOG.event(
            'thread_error',
            thread=getattr(args.thread, 'name', ''),
            error_type=getattr(args.exc_type, '__name__', str(args.exc_type)),
            error=str(args.exc_value),
            traceback=''.join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)),
        )

    threading.excepthook = _thread_hook


if __name__ == '__main__':
    App().mainloop()
