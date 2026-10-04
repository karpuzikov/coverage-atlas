from __future__ import annotations

import os
import sys
import re
import ast
import json
import math
import time
import random
import hashlib
import shutil
import threading
import subprocess
import traceback
import platform
import importlib
import urllib.request
from pathlib import Path
from dataclasses import dataclass, field
from collections import Counter
from itertools import combinations

APP = 'Coverage Atlas Reasoning Lab'
VERSION = '0.1.10'
STATUS = 'Under construction ⚠️'


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
LOGS = DATA / 'logs'
SESSIONS = DATA / 'sessions'
EXPORTS = DATA / 'exports'
SHARE = DATA / 'Files to Send ChatGPT'
TEMP = DATA / 'temp'
DEPS = DATA / 'dependencies'
RULES_FILE = DATA / 'selection_rules.json'
for folder in (LOGS, SESSIONS, EXPORTS, SHARE, TEMP, DEPS):
    folder.mkdir(parents=True, exist_ok=True)

if str(DEPS) not in sys.path:
    sys.path.insert(0, str(DEPS))


class Diagnostics:
    def __init__(self):
        stamp = time.strftime('%Y-%m-%d-%H-%M-%S')
        self.path = LOGS / f'{APP} {stamp}.jsonl'
        self.lock = threading.RLock()
        self._fh = None
        try:
            self._fh = self.path.open('a', encoding='utf-8', buffering=1)
        except Exception:
            pass
        self.event(
            'app_start',
            version=VERSION,
            python=sys.version.split()[0],
            executable=sys.executable,
            platform=platform.platform(),
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
            with self.lock:
                self._fh.write(json.dumps(row, ensure_ascii=False, default=str) + '\n')
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
    bundle = TEMP / 'Microsoft.DesktopAppInstaller.msixbundle'
    cmd = [
        ps,
        '-NoProfile',
        '-ExecutionPolicy',
        'Bypass',
        '-Command',
        "$ProgressPreference='SilentlyContinue'; "
        f"Invoke-WebRequest -UseBasicParsing 'https://aka.ms/getwinget' -OutFile '{bundle}'; "
        f"Add-AppxPackage '{bundle}'",
    ]
    try:
        subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=(0x08000000 if os.name == 'nt' else 0),
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


LOGCHECKER_RELEASE_API = 'https://api.github.com/repos/OPSnet/Logchecker/releases/latest'
LOGCHECKER_DIR = DEPS / 'logchecker'
LOGCHECKER_PHAR = LOGCHECKER_DIR / 'logchecker.phar'
LOGCHECKER_RELEASE_STATE = DATA / 'cache' / 'logchecker-release.json'
LOGCHECKER_RESULT_CACHE = DATA / 'cache' / 'logchecker-results.json'
LOGCHECKER_PYTHON_PREFIX = DEPS / 'logchecker-python'
_LOGCHECKER_RUNTIME = None
_LOGCHECKER_LOCK = threading.RLock()


def find_php_cli():
    candidates = []
    found = shutil.which('php')
    if found:
        candidates.append(Path(found))
    local = os.environ.get('LOCALAPPDATA')
    if local:
        base = Path(local)
        candidates.extend([
            base / 'Microsoft' / 'WinGet' / 'Links' / 'php.exe',
            base / 'Programs' / 'PHP' / 'php.exe',
        ])
        packages = base / 'Microsoft' / 'WinGet' / 'Packages'
        if packages.exists():
            for package in packages.glob('PHP.PHP.8.4*'):
                candidates.extend(package.rglob('php.exe'))
    for candidate in candidates:
        try:
            if candidate.is_file():
                return str(candidate)
        except Exception:
            pass
    return None


def ensure_php_cli():
    php = find_php_cli()
    if php or os.name != 'nt':
        return php
    winget = shutil.which('winget')
    if not winget:
        ensure_winget()
        winget = shutil.which('winget')
    if not winget:
        local = os.environ.get('LOCALAPPDATA')
        candidate = Path(local) / 'Microsoft' / 'WindowsApps' / 'winget.exe' if local else None
        if candidate and candidate.is_file():
            winget = str(candidate)
    if not winget:
        LOG.event('logchecker_php_unavailable', reason='winget_not_available')
        return None
    try:
        LOG.event('logchecker_php_install_start', package='PHP.PHP.8.4')
        subprocess.run(
            [winget, 'install', '--id', 'PHP.PHP.8.4', '--exact', '--silent',
             '--accept-package-agreements', '--accept-source-agreements'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300,
            check=False, creationflags=0x08000000,
        )
    except Exception as exc:
        LOG.error('logchecker_php_install_failed', exc)
    php = find_php_cli()
    LOG.event('logchecker_php_ready' if php else 'logchecker_php_unavailable', php=php)
    return php


def ensure_logchecker_phar():
    LOGCHECKER_DIR.mkdir(parents=True, exist_ok=True)
    state = {}
    try:
        state = json.loads(LOGCHECKER_RELEASE_STATE.read_text('utf-8'))
    except Exception:
        pass
    now = time.time()
    if LOGCHECKER_PHAR.is_file() and now - float(state.get('checked_at', 0) or 0) < 7 * 86400:
        return LOGCHECKER_PHAR, str(state.get('version') or 'cached')
    try:
        request = urllib.request.Request(
            LOGCHECKER_RELEASE_API,
            headers={'User-Agent': f'{APP}/{VERSION}', 'Accept': 'application/vnd.github+json'},
        )
        with urllib.request.urlopen(request, timeout=25) as response:
            release = json.loads(response.read().decode('utf-8'))
        asset = next((a for a in release.get('assets', []) if a.get('name') == 'logchecker.phar'), None)
        if not asset or not asset.get('browser_download_url'):
            raise RuntimeError('Latest Logchecker PHAR was not found.')
        with urllib.request.urlopen(asset['browser_download_url'], timeout=60) as response:
            payload = response.read()
        digest = hashlib.sha256(payload).hexdigest()
        expected = str(asset.get('digest') or '')
        if expected.startswith('sha256:') and digest != expected.split(':', 1)[1].lower():
            raise RuntimeError('Logchecker PHAR SHA-256 verification failed.')
        temp = LOGCHECKER_PHAR.with_suffix('.tmp')
        temp.write_bytes(payload)
        temp.replace(LOGCHECKER_PHAR)
        state = {
            'version': str(release.get('tag_name') or 'latest'),
            'checked_at': now,
            'sha256': digest,
        }
        LOGCHECKER_RELEASE_STATE.parent.mkdir(parents=True, exist_ok=True)
        LOGCHECKER_RELEASE_STATE.write_text(json.dumps(state, indent=2), encoding='utf-8')
        LOG.event('logchecker_phar_updated', version=state['version'], sha256=digest)
        return LOGCHECKER_PHAR, state['version']
    except Exception as exc:
        if LOGCHECKER_PHAR.is_file():
            LOG.error('logchecker_update_failed_using_cached', exc)
            return LOGCHECKER_PHAR, str(state.get('version') or 'cached')
        LOG.error('logchecker_phar_unavailable', exc)
        return None, None


def logchecker_environment():
    prefix = LOGCHECKER_PYTHON_PREFIX
    scripts = prefix / 'Scripts'
    site = prefix / 'Lib' / 'site-packages'
    if not ((scripts / 'eac_logchecker.exe').is_file() and (scripts / 'xld_logchecker.exe').is_file()):
        try:
            subprocess.run(
                [sys.executable, '-m', 'pip', 'install', '--upgrade', '--prefix', str(prefix),
                 'chardet', 'eac-logchecker', 'xld-logchecker'],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=240,
                creationflags=(0x08000000 if os.name == 'nt' else 0),
            )
            LOG.event('logchecker_python_helpers_ready')
        except Exception as exc:
            LOG.error('logchecker_python_helpers_install_failed', exc)
    env = os.environ.copy()
    env['PATH'] = str(scripts) + (os.pathsep + env['PATH'] if env.get('PATH') else '')
    env['PYTHONPATH'] = str(site) + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    env['PYTHON'] = sys.executable
    return env


def php_logchecker_options(php: str) -> list[str]:
    try:
        probe = subprocess.run(
            [php, '-r', "exit(extension_loaded('iconv') ? 0 : 1);"],
            capture_output=True, timeout=10,
            creationflags=(0x08000000 if os.name == 'nt' else 0),
        )
        if probe.returncode == 0:
            return []
    except Exception:
        pass
    try:
        php_root = Path(php).resolve().parent
        extension_dir = php_root / 'ext'
        if (extension_dir / 'php_iconv.dll').is_file():
            LOG.event('logchecker_php_iconv_enable', extension_dir=str(extension_dir))
            return ['-d', f'extension_dir={extension_dir}', '-d', 'extension=php_iconv.dll']
    except Exception as exc:
        LOG.error('logchecker_php_iconv_probe_failed', exc, php=php)
    LOG.event('logchecker_php_iconv_unavailable', php=php)
    return []


def ensure_logchecker_runtime():
    global _LOGCHECKER_RUNTIME
    with _LOGCHECKER_LOCK:
        if _LOGCHECKER_RUNTIME is False:
            return None
        if isinstance(_LOGCHECKER_RUNTIME, dict):
            return _LOGCHECKER_RUNTIME
        php = ensure_php_cli()
        phar, version = ensure_logchecker_phar() if php else (None, None)
        if not php or not phar:
            _LOGCHECKER_RUNTIME = False
            return None
        _LOGCHECKER_RUNTIME = {
            'php': php, 'php_options': php_logchecker_options(php),
            'phar': str(phar), 'version': version,
            'env': logchecker_environment(),
        }
        return _LOGCHECKER_RUNTIME


def analyze_logchecker_file(path: Path) -> dict:
    try:
        stat = path.stat()
        key = str(path.resolve()).casefold()
        try:
            cache = json.loads(LOGCHECKER_RESULT_CACHE.read_text('utf-8'))
        except Exception:
            cache = {}
        old = cache.get(key, {})
        same_file = old.get('size') == stat.st_size and old.get('mtime_ns') == stat.st_mtime_ns
        if same_file and old.get('score') is not None and time.time() - float(old.get('checked_at', 0) or 0) < 30 * 86400:
            return dict(old)
        if same_file and old.get('error') and time.time() - float(old.get('checked_at', 0) or 0) < 900:
            return dict(old)

        runtime = ensure_logchecker_runtime()
        if not runtime:
            result = {
                'path': str(path), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
                'score': None, 'checksum': None, 'details': [],
                'error': 'PHP or Logchecker could not be made available.', 'checked_at': time.time(),
            }
        else:
            token = hashlib.sha1(f'{key}|{stat.st_size}|{stat.st_mtime_ns}'.encode('utf-8')).hexdigest()[:20]
            html_out = TEMP / f'logchecker-{token}.html'
            json_out = TEMP / f'logchecker-{token}.json'
            try:
                completed = subprocess.run(
                    [runtime['php'], *runtime.get('php_options', []), runtime['phar'], 'analyze', '--no_text', str(path), str(html_out), str(json_out)],
                    capture_output=True, text=True, encoding='utf-8', errors='replace',
                    timeout=90, env=runtime['env'],
                    creationflags=(0x08000000 if os.name == 'nt' else 0),
                )
                parsed = json.loads(json_out.read_text('utf-8')) if json_out.is_file() else {}
                score = parsed.get('score')
                if score is None:
                    match = re.search(r'(?im)^Score\s*:\s*(\d+)\s*$', completed.stdout or '')
                    score = int(match.group(1)) if match else None
                result = {
                    'path': str(path), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
                    'score': float(score) if score is not None else None,
                    'checksum': parsed.get('checksum'), 'ripper': parsed.get('ripper'),
                    'ripper_version': parsed.get('version'), 'details': parsed.get('details') or [],
                    'checker_version': runtime['version'],
                    'error': None if score is not None else (completed.stderr or completed.stdout or f'Logchecker exit code {completed.returncode}').strip()[:1200],
                    'checked_at': time.time(),
                }
            except Exception as exc:
                result = {
                    'path': str(path), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
                    'score': None, 'checksum': None, 'details': [],
                    'error': f'{type(exc).__name__}: {exc}', 'checked_at': time.time(),
                }
            finally:
                for item in (html_out, json_out):
                    try:
                        item.unlink(missing_ok=True)
                    except Exception:
                        pass
        cache[key] = result
        LOGCHECKER_RESULT_CACHE.parent.mkdir(parents=True, exist_ok=True)
        temp_cache = LOGCHECKER_RESULT_CACHE.with_suffix('.tmp')
        temp_cache.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding='utf-8')
        temp_cache.replace(LOGCHECKER_RESULT_CACHE)
        LOG.event('logchecker_file_result', path=str(path), score=result.get('score'),
                  checksum=result.get('checksum'), ripper=result.get('ripper'), error=result.get('error'))
        return result
    except Exception as exc:
        LOG.error('logchecker_file_failed', exc, path=str(path))
        return {'path': str(path), 'score': None, 'checksum': None, 'details': [], 'error': str(exc)}


def score_release_logs(folder: str, log_paths: list[Path] | None = None) -> tuple[float | None, list[dict]]:
    root = Path(folder)
    if not root.is_dir():
        return None, []
    if log_paths is None:
        try:
            log_paths = sorted(
                p for p in root.rglob('*.log')
                if p.is_file() and not p.name.casefold().startswith('logchecker')
            )
        except Exception as exc:
            LOG.error('logchecker_log_discovery_failed', exc, folder=folder)
            return None, []
    if not log_paths:
        return None, []
    results = [analyze_logchecker_file(path) for path in log_paths]
    scores = [float(row['score']) for row in results if row.get('score') is not None]
    return (round(sum(scores) / len(scores), 2) if scores else None), results


import tkinter as tk
from tkinter import ttk, filedialog, messagebox


AUDIO = {
    '.flac', '.mp3', '.m4a', '.mp4', '.aac', '.ogg', '.opus', '.wav', '.wma',
    '.ape', '.wv', '.tta', '.mka', '.dsf', '.dff'
}

DISC_FOLDER_RE = re.compile(
    r'^(?:cd|disc|disk)\s*[-_ ]?0*\d+$',
    re.I,
)
ORGANIZATIONAL_FOLDER_RE = re.compile(
    r'^\d+\.\s*(?:albums?|compilations?|singles?|soundtracks?|bootlegs?|remixes?|tracks?)$',
    re.I,
)


def scalar_text(value) -> str:
    if value is None:
        return ''
    if hasattr(value, 'text'):
        return scalar_text(value.text)
    if hasattr(value, 'data') and isinstance(getattr(value, 'data', None), (bytes, bytearray, memoryview)):
        return scalar_text(value.data)
    if isinstance(value, (list, tuple)):
        for item in value:
            text = scalar_text(item)
            if text:
                return text
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
            text = scalar_text(tags.get(key))
            if text:
                return text
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


def compact(value: str) -> str:
    return ''.join(ch for ch in (value or '').casefold() if ch.isalnum())


def clean_identifier(value: str) -> str:
    return re.sub(r'[^a-z0-9]', '', (value or '').casefold())


def number_from_tag(value: str, default: int = 1) -> int:
    match = re.search(r'\d+', value or '')
    return int(match.group(0)) if match else default


def is_disc_folder(path: Path) -> bool:
    return bool(DISC_FOLDER_RE.fullmatch(path.name.strip()))


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
            if item.is_file():
                if item.suffix.casefold() == '.cue':
                    has_cue = True
                elif item.suffix.casefold() == '.log':
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
    trackno: str = ''
    discno: str = ''
    isrc: str = ''
    mbrec: str = ''
    extension: str = ''
    size_bytes: int = 0
    bitrate_kbps: int = 0

    def identity(self) -> str:
        mbid = clean_identifier(self.mbrec)
        if mbid:
            return 'mb:' + mbid
        isrc = clean_identifier(self.isrc)
        if isrc:
            return 'isrc:' + isrc
        rounded = int(round(self.duration)) if self.duration else 0
        return f'meta:{compact(self.artist)}:{compact(self.title)}:{rounded}'


@dataclass
class Release:
    rid: str
    folder: str
    relative_folder: str
    name: str
    date: str = ''
    barcode: str = ''
    albumartist: str = ''
    mbrel: str = ''
    source_container: str = ''
    media_type: str = 'UNKNOWN'
    media_reason: str = ''
    tracks: list[Track] = field(default_factory=list)
    logchecker_score: float | None = None
    logchecker_logs: list[dict] = field(default_factory=list)

    def feature_snapshot(self) -> dict:
        extensions = Counter(track.extension for track in self.tracks if track.extension)
        discs = sorted({number_from_tag(track.discno, 1) for track in self.tracks})
        bitrates = [track.bitrate_kbps for track in self.tracks if track.bitrate_kbps > 0]
        return {
            'release_id': self.rid,
            'name': self.name,
            'date': self.date,
            'barcode': self.barcode,
            'albumartist': self.albumartist,
            'musicbrainz_release_id': self.mbrel,
            'relative_folder': self.relative_folder,
            'source_container': self.source_container,
            'media_type': self.media_type,
            'media_reason': self.media_reason,
            'track_count': len(self.tracks),
            'disc_count': len(discs) if discs else 1,
            'total_duration_sec': round(sum(t.duration for t in self.tracks), 3),
            'total_size_bytes': sum(t.size_bytes for t in self.tracks),
            'extensions': dict(sorted(extensions.items())),
            'average_bitrate_kbps': (
                round(sum(bitrates) / len(bitrates), 1) if bitrates else 0
            ),
            'logchecker_score': self.logchecker_score,
            'logchecker_log_count': len(self.logchecker_logs),
            'logchecker_logs': self.logchecker_logs,
            'tracks': [
                {
                    'title': track.title,
                    'artist': track.artist,
                    'duration_sec': round(track.duration, 3),
                    'track_number': track.trackno,
                    'disc_number': track.discno,
                    'isrc': track.isrc,
                    'musicbrainz_recording_id': track.mbrec,
                    'extension': track.extension,
                    'size_bytes': track.size_bytes,
                    'bitrate_kbps': track.bitrate_kbps,
                    'relative_path': track.relative_path,
                    'identity': track.identity(),
                }
                for track in self.tracks
            ],
        }


def scan_releases(root: Path, progress=lambda done, total: None) -> dict[str, Release]:
    files = sorted(
        path for path in root.rglob('*')
        if path.is_file() and path.suffix.casefold() in AUDIO
    )
    LOG.event('scan_start', root=str(root), audio_file_count=len(files))

    groups: dict[str, Release] = {}
    failures = 0

    for index, path in enumerate(files, 1):
        progress(index, len(files))
        try:
            media = MFile(path, easy=False)
            tags = getattr(media, 'tags', None) or {}
            info = getattr(media, 'info', None)
            duration = float(getattr(info, 'length', 0) or 0)
            bitrate = int(round(float(getattr(info, 'bitrate', 0) or 0) / 1000))

            title = tag(tags, 'title') or path.stem
            artist = tag(tags, 'artist')
            album = tag(tags, 'album') or path.parent.name
            albumartist = tag(tags, 'albumartist')
            date = tag(tags, 'date')
            barcode = tag(tags, 'barcode')
            mbrel = tag(tags, 'mbrel')
            isrc = tag(tags, 'isrc')
            mbrec = tag(tags, 'mbrec')
            discno = tag(tags, 'disc')
            trackno = tag(tags, 'track')

            release_folder = release_folder_for(path, root)
            source_container = source_container_for(release_folder, root)
            media_type, media_reason = classify_media_type(
                release_folder,
                source_container,
            )

            try:
                relative_folder = str(release_folder.relative_to(root))
            except Exception:
                relative_folder = release_folder.name

            # Keep separate tagged releases separate even when they share an
            # organizational container folder.
            release_seed = '|'.join([
                relative_folder.casefold(),
                clean_identifier(mbrel),
                compact(album),
                clean_identifier(barcode),
            ])
            rid = hashlib.sha1(release_seed.encode('utf-8', 'ignore')).hexdigest()[:20]

            if rid not in groups:
                groups[rid] = Release(
                    rid=rid,
                    folder=str(release_folder),
                    relative_folder=relative_folder,
                    name=album,
                    date=date,
                    barcode=barcode,
                    albumartist=albumartist,
                    mbrel=mbrel,
                    source_container=source_container,
                    media_type=media_type,
                    media_reason=media_reason,
                )

            groups[rid].tracks.append(
                Track(
                    path=str(path),
                    relative_path=str(path.relative_to(root)),
                    title=title,
                    artist=artist,
                    duration=duration,
                    trackno=trackno,
                    discno=discno,
                    isrc=isrc,
                    mbrec=mbrec,
                    extension=path.suffix.casefold(),
                    size_bytes=path.stat().st_size,
                    bitrate_kbps=bitrate,
                )
            )
        except Exception as exc:
            failures += 1
            LOG.error('scan_file_error', exc, path=str(path))

    for release in groups.values():
        release.tracks.sort(
            key=lambda track: (
                number_from_tag(track.discno, 1),
                number_from_tag(track.trackno, 999999),
                track.relative_path.casefold(),
            )
        )

    logchecker_scored, logchecker_candidates = score_duplicate_release_logs(groups.values())

    LOG.event(
        'scan_complete',
        root=str(root),
        release_count=len(groups),
        audio_file_count=len(files),
        file_failures=failures,
        logchecker_scored_release_count=logchecker_scored,
        logchecker_candidate_release_count=logchecker_candidates,
        releases=[release.feature_snapshot() for release in groups.values()],
    )
    return groups


def pair_id(left: str, right: str) -> str:
    a, b = sorted((left, right))
    return hashlib.sha1(f'{a}|{b}'.encode('utf-8')).hexdigest()[:24]


def release_set_hash(releases: dict[str, Release]) -> str:
    payload = '|'.join(sorted(releases))
    return hashlib.sha1(payload.encode('utf-8')).hexdigest()[:16]


def path_hash(root: Path) -> str:
    try:
        text = str(root.resolve())
    except Exception:
        text = str(root)
    return hashlib.sha1(text.casefold().encode('utf-8')).hexdigest()[:12]


def comparison_snapshot(left: Release, right: Release) -> dict:
    left_ids = {semantic_identity(track) for track in left.tracks}
    right_ids = {semantic_identity(track) for track in right.tracks}
    shared = left_ids & right_ids
    return {
        'shared_identity_count': len(shared),
        'left_only_identity_count': len(left_ids - right_ids),
        'right_only_identity_count': len(right_ids - left_ids),
        'left_track_count': len(left.tracks),
        'right_track_count': len(right.tracks),
        'left_total_duration_sec': round(sum(t.duration for t in left.tracks), 3),
        'right_total_duration_sec': round(sum(t.duration for t in right.tracks), 3),
        'left_media_type': left.media_type,
        'right_media_type': right.media_type,
        'same_barcode': bool(left.barcode and right.barcode and left.barcode == right.barcode),
        'same_musicbrainz_release_id': bool(
            left.mbrel and right.mbrel and left.mbrel == right.mbrel
        ),
        'left_logchecker_score': left.logchecker_score,
        'right_logchecker_score': right.logchecker_score,
        'logchecker_comparable': left.logchecker_score is not None and right.logchecker_score is not None,
    }


def meaningful_pair(left: Release, right: Release) -> bool:
    left_ids = {semantic_identity(track) for track in left.tracks}
    right_ids = {semantic_identity(track) for track in right.tracks}
    if left_ids & right_ids:
        return True

    if left.mbrel and right.mbrel and clean_identifier(left.mbrel) == clean_identifier(right.mbrel):
        return True

    if left.barcode and right.barcode and clean_identifier(left.barcode) == clean_identifier(right.barcode):
        return True

    # Conservative fallback for editions whose identifiers are absent but which
    # are clearly variants of the same release.
    same_artist = bool(
        compact(left.albumartist) and
        compact(left.albumartist) == compact(right.albumartist)
    )
    same_name = bool(
        compact(left.name) and
        compact(left.name) == compact(right.name)
    )
    return same_artist and same_name


def build_meaningful_pairs(releases: dict[str, Release]) -> list[list[str]]:
    release_ids = sorted(releases)
    return [
        [left_id, right_id]
        for left_id, right_id in combinations(release_ids, 2)
        if meaningful_pair(releases[left_id], releases[right_id])
    ]



REMIX_TITLE_RE = re.compile(
    r'(?<![a-z0-9])(?:remix(?:es|ed)?|rmx|dub|mashup|bootleg)(?![a-z0-9])',
    re.I,
)
LIVE_TITLE_RE = re.compile(
    r'(?<![a-z0-9])live(?:\s+acoustic)?(?![a-z0-9])',
    re.I,
)
SNIPPET_TITLE_RE = re.compile(
    r'(?<![a-z0-9])(?:snippet|excerpt|preview|callout|hook)(?![a-z0-9])',
    re.I,
)
FEATURE_TITLE_RE = re.compile(
    r'(?<![a-z0-9])(?:feat(?:uring)?\.?|ft\.?)\s+',
    re.I,
)
VERSION_TITLE_RE = re.compile(
    r'(?<![a-z0-9])(?:version|acoustic)(?![a-z0-9])',
    re.I,
)


def semantic_identity(track: Track) -> str:
    mbid = clean_identifier(track.mbrec)
    if mbid:
        return 'mb:' + mbid

    isrc = clean_identifier(track.isrc)
    if isrc:
        return 'isrc:' + isrc

    # Duration is deliberately not part of the fallback identity. The training
    # data showed the same song differing by ~1 second between editions, which
    # must still be treated as the same recording for coverage comparison.
    return f'meta:{compact(track.artist)}:{compact(track.title)}'


def score_duplicate_release_logs(releases):
    groups = {}
    for release in releases:
        if release.media_type != 'CD' or not release.tracks:
            continue
        signature = tuple(sorted(
            (semantic_identity(track), int(round(track.duration)) if track.duration else 0)
            for track in release.tracks
        ))
        groups.setdefault(signature, []).append(release)

    scored = 0
    candidates_scored = 0
    for group in groups.values():
        if len(group) < 2:
            continue
        logs_by_release = {}
        for release in group:
            try:
                paths = sorted(
                    path for path in Path(release.folder).rglob('*.log')
                    if path.is_file() and not path.name.casefold().startswith('logchecker')
                )
            except Exception as exc:
                LOG.error('logchecker_log_discovery_failed', exc, folder=release.folder)
                paths = []
            if paths:
                logs_by_release[release.rid] = paths
        if len(logs_by_release) < 2:
            continue
        candidates_scored += len(logs_by_release)
        for release in group:
            paths = logs_by_release.get(release.rid)
            if not paths:
                continue
            release.logchecker_score, release.logchecker_logs = score_release_logs(release.folder, paths)
            scored += int(release.logchecker_score is not None)
            LOG.event(
                'release_logchecker_summary',
                release_id=release.rid,
                release=release.name,
                score=release.logchecker_score,
                log_count=len(release.logchecker_logs),
                logs=release.logchecker_logs,
            )
    return scored, candidates_scored


def is_remix_track(track: Track) -> bool:
    return bool(REMIX_TITLE_RE.search(track.title or ''))


def is_live_track(track: Track) -> bool:
    return bool(LIVE_TITLE_RE.search(track.title or ''))


def is_snippet_track(track: Track) -> bool:
    # Promo snippets are often stored under a release folder named
    # "Snippet Sampler" while the individual audio files have ordinary
    # song titles. Check both the title and the release's relative path.
    if SNIPPET_TITLE_RE.search(track.title or ''):
        return True

    path_parts = re.split(r'[\\/]+', track.relative_path or track.path or '')
    # Ignore the filename itself here; title-level matching above handles it.
    # Do not treat a generic "Sampler" folder as snippets: some samplers hold
    # full-length songs. Require an explicit snippet/excerpt/preview/callout
    # label in a parent path component.
    return any(
        SNIPPET_TITLE_RE.search(part)
        for part in path_parts[:-1]
    )


def has_snippet_release_path_label(release: Release) -> bool:
    for track in release.tracks:
        path_parts = re.split(r'[\\/]+', track.relative_path or track.path or '')
        if any(
            SNIPPET_TITLE_RE.search(part)
            for part in path_parts[:-1]
        ):
            return True
    return False


def is_featured_track(track: Track) -> bool:
    title = track.title or ''
    artist = track.artist or ''
    return bool(
        FEATURE_TITLE_RE.search(title) or
        FEATURE_TITLE_RE.search(artist)
    )


def is_version_track(track: Track) -> bool:
    return bool(VERSION_TITLE_RE.search(track.title or ''))


def track_selection_class(track: Track) -> str:
    if is_snippet_track(track):
        return 'ignored_snippet'
    if is_live_track(track):
        return 'ignored_live'
    if is_remix_track(track):
        # Learned exception: a remix with an additional featured artist counts
        # as unique musical content.
        if is_featured_track(track):
            return 'valuable_featured_remix'
        return 'ignored_remix'
    if is_version_track(track):
        return 'valuable_version'
    return 'valuable'


def track_counts_for_selection(track: Track) -> bool:
    return track_selection_class(track).startswith('valuable')


def unique_tracks(release: Release, other: Release) -> list[Track]:
    other_ids = {semantic_identity(track) for track in other.tracks}
    return [
        track
        for track in release.tracks
        if semantic_identity(track) not in other_ids
    ]


def non_remix_unique_tracks(release: Release, other: Release) -> list[Track]:
    # Historical name kept for session compatibility. This now means tracks
    # that count toward the user's release-selection coverage.
    return [
        track
        for track in unique_tracks(release, other)
        if track_counts_for_selection(track)
    ]


def remix_unique_tracks(release: Release, other: Release) -> list[Track]:
    return [
        track
        for track in unique_tracks(release, other)
        if track_selection_class(track) == 'ignored_remix'
    ]


def valuable_track_map(release: Release) -> dict[str, Track]:
    return {
        semantic_identity(track): track
        for track in release.tracks
        if track_counts_for_selection(track)
    }


def ignored_track_count(release: Release, kind: str | None = None) -> int:
    count = 0
    for track in release.tracks:
        classification = track_selection_class(track)
        if not classification.startswith('ignored_'):
            continue
        if kind and classification != kind:
            continue
        count += 1
    return count


def support_file_names(release: Release) -> list[str]:
    result = []
    folder = Path(release.folder)
    try:
        candidates = list(folder.iterdir())
        for child in list(candidates):
            if child.is_dir() and is_disc_folder(child):
                try:
                    candidates.extend(child.iterdir())
                except Exception:
                    pass

        for item in candidates:
            if not item.is_file():
                continue
            name = item.name.casefold()
            if (
                item.suffix.casefold() in {'.cue', '.log', '.accurip', '.md5', '.sfv'}
                or 'audiochecker' in name
            ):
                result.append(item.name)
    except Exception:
        pass
    return sorted(set(result), key=str.casefold)


def reason_categories(text: str) -> set[str]:
    value = (text or '').casefold()
    categories = set()
    if not value:
        return categories

    if re.search(r'\b(?:included|include|unique|contains?|coverage|opposite tracks?)\b', value):
        categories.add('wanted_track_coverage')
    if re.search(r'\b(?:remix|rmx|dub)s?\b', value):
        categories.add('ignore_remix_extras')
    if re.search(r'\b(?:lower|fewer|less)\b.*\btrack', value) or 'track number overall' in value:
        categories.add('fewer_tracks')
    if re.search(r'\b(?:audiochecker|cue|log|complete|completeness)\b', value):
        categories.add('package_completeness')
    if re.search(r'\b(?:cd|physical)\b', value):
        categories.add('prefer_cd')
    if re.search(r'\b(?:lossless|flac|wav|ape)\b', value):
        categories.add('prefer_lossless')

    return categories


def historical_reason_profile(answers: dict) -> Counter:
    profile = Counter()
    for answer in answers.values():
        motivation = answer.get('motivation') or {}
        for category in motivation.get('categories') or []:
            profile[str(category)] += 1

        for category in reason_categories(str(answer.get('reason', '') or '')):
            profile[category] += 1
    return profile


def compact_track_names(tracks: list[Track], limit: int = 3) -> str:
    names = [track.title or Path(track.path).stem for track in tracks]
    if len(names) <= limit:
        return ', '.join(names)
    return ', '.join(names[:limit]) + f' (+{len(names) - limit} more)'


def infer_choice_motivation(
    chosen: Release,
    other: Release,
    historical_answers: dict,
) -> dict:
    chosen_wanted = non_remix_unique_tracks(chosen, other)
    other_wanted = non_remix_unique_tracks(other, chosen)
    chosen_remixes = remix_unique_tracks(chosen, other)
    other_remixes = remix_unique_tracks(other, chosen)
    chosen_support = support_file_names(chosen)
    other_support = support_file_names(other)
    profile = historical_reason_profile(historical_answers)

    candidates = []
    counter_evidence = []

    def add(category: str, summary: str, confidence: float):
        learned = profile.get(category, 0)
        confidence = min(0.99, confidence + min(0.06, learned * 0.01))
        candidates.append({
            'category': category,
            'summary': summary,
            'confidence': round(confidence, 3),
            'historical_support': learned,
        })

    if chosen_wanted and not other_wanted:
        add(
            'wanted_track_coverage',
            (
                'Chosen release contains additional non-remix track(s): '
                + compact_track_names(chosen_wanted)
            ),
            0.96,
        )
    elif len(chosen_wanted) > len(other_wanted):
        add(
            'wanted_track_coverage',
            (
                f'Chosen release has more additional non-remix tracks '
                f'({len(chosen_wanted)} vs {len(other_wanted)}).'
            ),
            0.89,
        )

    if other_wanted:
        counter_evidence.append(
            f'Other release also has {len(other_wanted)} additional non-remix track(s): '
            + compact_track_names(other_wanted)
        )

    if other_remixes and not other_wanted:
        add(
            'ignore_remix_extras',
            (
                'The other release only adds remix-type track(s), which appear '
                'non-essential: ' + compact_track_names(other_remixes)
            ),
            0.93,
        )

    chosen_core = set(valuable_track_map(chosen))
    other_core = set(valuable_track_map(other))

    if chosen_core == other_core and len(chosen.tracks) < len(other.tracks):
        add(
            'fewer_tracks',
            (
                f'Same non-remix coverage with fewer files/tracks '
                f'({len(chosen.tracks)} vs {len(other.tracks)}).'
            ),
            0.90,
        )

    if (
        chosen_core == other_core
        and len(chosen_support) > len(other_support)
        and chosen_support
    ):
        add(
            'package_completeness',
            (
                'Chosen release has more supporting rip/check files: '
                + ', '.join(chosen_support)
            ),
            0.88,
        )

    if chosen_core == other_core and chosen.media_type == 'CD' and other.media_type != 'CD':
        add(
            'prefer_cd',
            'Musical coverage is equivalent and the chosen release is identified as CD.',
            0.82,
        )

    if (
        chosen_core == other_core
        and release_lossless(chosen)
        and not release_lossless(other)
    ):
        add(
            'prefer_lossless',
            'Musical coverage is equivalent and the chosen release is lossless.',
            0.80,
        )

    if (
        chosen_core == other_core
        and not candidates
        and len(chosen.tracks) == len(other.tracks)
    ):
        counter_evidence.append('The measurable musical coverage is effectively identical.')

    candidates.sort(
        key=lambda item: (
            -float(item['confidence']),
            -int(item['historical_support']),
            str(item['category']),
        )
    )

    strong = [item for item in candidates if item['confidence'] >= 0.88]
    combined_categories = [item['category'] for item in strong]
    combined_summary = ' '.join(item['summary'] for item in strong)

    # Ask whenever the other side also has non-remix content that the chosen
    # side does not have. That is a real trade-off, so guessing motivation would
    # be unsafe. Otherwise a strong one-directional explanation can be inferred.
    needs_user = bool(other_wanted) or not candidates

    if strong and not other_wanted:
        needs_user = False

    confidence = (
        max(float(item['confidence']) for item in candidates)
        if candidates else 0.0
    )

    return {
        'mode': 'needs_user' if needs_user else 'inferred',
        'confidence': round(confidence, 3),
        'categories': combined_categories or [
            item['category'] for item in candidates[:2]
        ],
        'summary': combined_summary or (
            candidates[0]['summary'] if candidates else ''
        ),
        'candidates': candidates,
        'counter_evidence': counter_evidence,
        'chosen_unique_non_remix': [
            track.title for track in chosen_wanted
        ],
        'other_unique_non_remix': [
            track.title for track in other_wanted
        ],
        'chosen_unique_remix': [
            track.title for track in chosen_remixes
        ],
        'other_unique_remix': [
            track.title for track in other_remixes
        ],
        'chosen_support_files': chosen_support,
        'other_support_files': other_support,
        'historical_profile': dict(profile),
    }



def infer_keep_both_motivation(
    left: Release,
    right: Release,
) -> dict:
    left_unique = non_remix_unique_tracks(left, right)
    right_unique = non_remix_unique_tracks(right, left)

    if left_unique and right_unique:
        return {
            'mode': 'inferred',
            'confidence': 0.99,
            'categories': ['keep_both_unique_coverage'],
            'summary': (
                'Keep both: each release contains valuable unique track(s). '
                f'A only: {compact_track_names(left_unique)}. '
                f'B only: {compact_track_names(right_unique)}.'
            ),
            'left_unique_valuable': [track.title for track in left_unique],
            'right_unique_valuable': [track.title for track in right_unique],
        }

    return {
        'mode': 'needs_user',
        'confidence': 0.0,
        'categories': [],
        'summary': '',
        'left_unique_valuable': [track.title for track in left_unique],
        'right_unique_valuable': [track.title for track in right_unique],
    }


ALLOWED_RULE_AST = (
    ast.Expression,
    ast.BoolOp,
    ast.BinOp,
    ast.UnaryOp,
    ast.Compare,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.List,
    ast.Tuple,
    ast.Set,
    ast.And,
    ast.Or,
    ast.Not,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
    ast.USub,
    ast.UAdd,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
    ast.Is,
    ast.IsNot,
)


def release_lossless(release: Release) -> bool:
    lossless = {'.flac', '.wav', '.ape', '.wv', '.tta', '.alac'}
    return any(track.extension in lossless for track in release.tracks)


def rule_context(left: Release, right: Release) -> dict:
    compare = comparison_snapshot(left, right)
    left_snap = left.feature_snapshot()
    right_snap = right.feature_snapshot()

    left_valuable = valuable_track_map(left)
    right_valuable = valuable_track_map(right)
    left_valuable_ids = set(left_valuable)
    right_valuable_ids = set(right_valuable)
    left_only_valuable_ids = left_valuable_ids - right_valuable_ids
    right_only_valuable_ids = right_valuable_ids - left_valuable_ids

    return {
        'shared': compare['shared_identity_count'],
        'left_unique': compare['left_only_identity_count'],
        'right_unique': compare['right_only_identity_count'],
        'left_valuable_count': len(left_valuable_ids),
        'right_valuable_count': len(right_valuable_ids),
        'left_unique_valuable': len(left_only_valuable_ids),
        'right_unique_valuable': len(right_only_valuable_ids),
        'same_valuable_coverage': left_valuable_ids == right_valuable_ids,
        'left_logchecker_score': left.logchecker_score,
        'right_logchecker_score': right.logchecker_score,
        'logchecker_comparable': left.logchecker_score is not None and right.logchecker_score is not None,
        'left_snippet_release': has_snippet_release_path_label(left),
        'right_snippet_release': has_snippet_release_path_label(right),
        'left_ignored_count': ignored_track_count(left),
        'right_ignored_count': ignored_track_count(right),
        'left_ignored_live': ignored_track_count(left, 'ignored_live'),
        'right_ignored_live': ignored_track_count(right, 'ignored_live'),
        'left_ignored_remix': ignored_track_count(left, 'ignored_remix'),
        'right_ignored_remix': ignored_track_count(right, 'ignored_remix'),
        'left_ignored_snippet': ignored_track_count(left, 'ignored_snippet'),
        'right_ignored_snippet': ignored_track_count(right, 'ignored_snippet'),
        'left_featured_remix_count': sum(
            1 for track in left.tracks
            if track_selection_class(track) == 'valuable_featured_remix'
        ),
        'right_featured_remix_count': sum(
            1 for track in right.tracks
            if track_selection_class(track) == 'valuable_featured_remix'
        ),
        'left_version_count': sum(
            1 for track in left.tracks
            if track_selection_class(track) == 'valuable_version'
        ),
        'right_version_count': sum(
            1 for track in right.tracks
            if track_selection_class(track) == 'valuable_version'
        ),
        'left_track_count': compare['left_track_count'],
        'right_track_count': compare['right_track_count'],
        'left_duration': compare['left_total_duration_sec'],
        'right_duration': compare['right_total_duration_sec'],
        'left_media_type': left.media_type,
        'right_media_type': right.media_type,
        'left_disc_count': left_snap['disc_count'],
        'right_disc_count': right_snap['disc_count'],
        'left_size': left_snap['total_size_bytes'],
        'right_size': right_snap['total_size_bytes'],
        'left_avg_bitrate': left_snap['average_bitrate_kbps'],
        'right_avg_bitrate': right_snap['average_bitrate_kbps'],
        'left_lossless': release_lossless(left),
        'right_lossless': release_lossless(right),
        'left_has_barcode': bool(left.barcode),
        'right_has_barcode': bool(right.barcode),
        'left_has_mbrel': bool(left.mbrel),
        'right_has_mbrel': bool(right.mbrel),
        'same_barcode': compare['same_barcode'],
        'same_musicbrainz_release_id': compare['same_musicbrainz_release_id'],
        'left_source': left.source_container,
        'right_source': right.source_container,
        'left_name': left.name,
        'right_name': right.name,
        'left_date': left.date,
        'right_date': right.date,
    }


def safe_rule_expression(expression: str, context: dict) -> bool:
    tree = ast.parse(expression, mode='eval')
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_RULE_AST):
            raise ValueError(f'Unsupported rule expression element: {type(node).__name__}')
        if isinstance(node, ast.Name) and node.id not in context:
            raise ValueError(f'Unknown rule field: {node.id}')
    return bool(eval(
        compile(tree, '<selection-rule>', 'eval'),
        {'__builtins__': {}},
        dict(context),
    ))


class SelectionRulePack:
    def __init__(self, data: dict, source_path: Path):
        self.data = data
        self.source_path = source_path
        self.name = str(data.get('name') or source_path.stem)
        self.version = str(data.get('version') or '1')
        self.rules = list(data.get('rules') or [])
        raw = json.dumps(data, ensure_ascii=False, sort_keys=True).encode('utf-8')
        self.hash = hashlib.sha1(raw).hexdigest()[:16]

    @classmethod
    def load(cls, path: Path):
        data = json.loads(path.read_text('utf-8'))
        if not isinstance(data, dict):
            raise ValueError('Selection rules file must contain a JSON object.')
        rules = data.get('rules')
        if not isinstance(rules, list):
            raise ValueError('Selection rules file must contain a "rules" array.')

        for index, rule in enumerate(rules, 1):
            if not isinstance(rule, dict):
                raise ValueError(f'Rule {index} is not an object.')
            expression = str(rule.get('when') or '').strip()
            choose = str(rule.get('choose') or '').strip().lower()
            if not expression:
                raise ValueError(f'Rule {index} has no "when" expression.')
            if choose not in {'left', 'right', 'both', 'skip'}:
                raise ValueError(
                    f'Rule {index} has invalid "choose": {choose!r}. '
                    'Use left, right, both, or skip.'
                )
            # Parse and validate against a broad dummy context at load time.
            dummy = {
                'shared': 0,
                'left_unique': 0,
                'right_unique': 0,
                'left_valuable_count': 0,
                'right_valuable_count': 0,
                'left_unique_valuable': 0,
                'right_unique_valuable': 0,
                'same_valuable_coverage': False,
                'left_snippet_release': False,
                'right_snippet_release': False,
                'left_ignored_count': 0,
                'right_ignored_count': 0,
                'left_ignored_live': 0,
                'right_ignored_live': 0,
                'left_ignored_remix': 0,
                'right_ignored_remix': 0,
                'left_ignored_snippet': 0,
                'right_ignored_snippet': 0,
                'left_featured_remix_count': 0,
                'right_featured_remix_count': 0,
                'left_version_count': 0,
                'right_version_count': 0,
                'left_track_count': 0,
                'right_track_count': 0,
                'left_duration': 0.0,
                'right_duration': 0.0,
                'left_media_type': '',
                'right_media_type': '',
                'left_disc_count': 0,
                'right_disc_count': 0,
                'left_size': 0,
                'right_size': 0,
                'left_avg_bitrate': 0.0,
                'right_avg_bitrate': 0.0,
                'left_lossless': False,
                'right_lossless': False,
                'left_has_barcode': False,
                'right_has_barcode': False,
                'left_has_mbrel': False,
                'right_has_mbrel': False,
                'same_barcode': False,
                'same_musicbrainz_release_id': False,
                'left_logchecker_score': None,
                'right_logchecker_score': None,
                'logchecker_comparable': False,
                'left_source': '',
                'right_source': '',
                'left_name': '',
                'right_name': '',
                'left_date': '',
                'right_date': '',
            }
            safe_rule_expression(expression, dummy)

        return cls(data, path)

    def evaluate(self, left: Release, right: Release):
        context = rule_context(left, right)
        for index, rule in enumerate(self.rules, 1):
            expression = str(rule.get('when') or '').strip()
            if safe_rule_expression(expression, context):
                return {
                    'rule_index': index,
                    'rule_id': str(rule.get('id') or f'rule_{index}'),
                    'description': str(rule.get('description') or ''),
                    'when': expression,
                    'choice': str(rule.get('choose')).lower(),
                    'context': context,
                }
        return None


class Session:
    def __init__(self, root: Path, releases: dict[str, Release]):
        self.root = root
        self.releases = releases
        self.release_hash = release_set_hash(releases)
        self.session_id = f'{path_hash(root)}-{self.release_hash}'
        self.path = SESSIONS / f'{self.session_id}.json'
        self.dataset_path = EXPORTS / f'Coverage Atlas Reasoning Dataset {self.session_id}.jsonl'
        self.data = self._load_or_create()
        self._rewrite_dataset()

    def _load_or_create(self) -> dict:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text('utf-8'))
                if (
                    data.get('release_set_hash') == self.release_hash and
                    data.get('root') == str(self.root)
                ):
                    data.setdefault('release_notes', {})
                    schema = int(data.get('schema', 1) or 1)

                    if schema < 3:
                        old_pairs = data.get('pairs', [])
                        allowed = {
                            pair_id(left, right)
                            for left, right in build_meaningful_pairs(self.releases)
                        }
                        data['pairs'] = [
                            pair
                            for pair in old_pairs
                            if len(pair) == 2 and pair_id(pair[0], pair[1]) in allowed
                        ]
                        data['schema'] = 3
                        data.setdefault('rounds', [])
                        data['rounds'].append({
                            'type': 'pair_pool_migration',
                            'at': time.strftime('%Y-%m-%dT%H:%M:%S'),
                            'from_schema': schema,
                            'meaningful_pair_count': len(data['pairs']),
                        })
                        self.data = data
                        self.save()

                    LOG.event(
                        'session_resumed',
                        session_id=self.session_id,
                        answered_count=len(data.get('answers', {})),
                        total_pairs=len(data.get('pairs', [])),
                        release_note_count=len(data.get('release_notes', {})),
                        schema=data.get('schema'),
                    )
                    return data
            except Exception as exc:
                LOG.error('session_load_error', exc, session_path=str(self.path))

        pairs = build_meaningful_pairs(self.releases)
        seed = int(hashlib.sha1(self.session_id.encode('utf-8')).hexdigest()[:12], 16)
        rng = random.Random(seed)
        rng.shuffle(pairs)
        for pair in pairs:
            if rng.random() < 0.5:
                pair.reverse()

        data = {
            'schema': 3,
            'app': APP,
            'version_created': VERSION,
            'root': str(self.root),
            'release_set_hash': self.release_hash,
            'session_id': self.session_id,
            'created_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'updated_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'seed': seed,
            'pairs': pairs,
            'answers': {},
            'release_notes': {},
            'rounds': [{
                'type': 'initial_training',
                'started_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
                'pair_count': len(pairs),
            }],
            'release_index': {
                rid: release.feature_snapshot()
                for rid, release in self.releases.items()
            },
        }
        self.data = data
        self.save()
        LOG.event(
            'session_created',
            session_id=self.session_id,
            release_count=len(self.releases),
            total_pairs=len(pairs),
            root=str(self.root),
        )
        return data

    @property
    def answers(self) -> dict:
        return self.data.setdefault('answers', {})

    @property
    def release_notes(self) -> dict:
        return self.data.setdefault('release_notes', {})

    def get_release_note(self, release_id: str) -> str:
        note = self.release_notes.get(release_id, {})
        if isinstance(note, dict):
            return str(note.get('text', '') or '')
        return str(note or '')

    def set_release_note(self, release_id: str, text: str):
        text = (text or '').strip()
        previous = self.get_release_note(release_id)
        if text == previous:
            return

        if text:
            self.release_notes[release_id] = {
                'text': text,
                'updated_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
            }
        else:
            self.release_notes.pop(release_id, None)

        self.save()
        self._rewrite_dataset()

        release = self.releases.get(release_id)
        LOG.event(
            'release_note_updated',
            session_id=self.session_id,
            release_id=release_id,
            release=release.feature_snapshot() if release else None,
            previous_note=previous,
            note=text,
        )

    def reusable_texts(self) -> list[str]:
        counts = Counter()
        latest = {}

        for answer in self.answers.values():
            text = str(answer.get('reason', '') or '').strip()
            if text:
                counts[text] += 1
                latest[text] = str(answer.get('answered_at', '') or '')

        for note in self.release_notes.values():
            if isinstance(note, dict):
                text = str(note.get('text', '') or '').strip()
                updated = str(note.get('updated_at', '') or '')
            else:
                text = str(note or '').strip()
                updated = ''
            if text:
                counts[text] += 1
                latest[text] = max(latest.get(text, ''), updated)

        return sorted(
            counts,
            key=lambda text: (
                -counts[text],
                latest.get(text, ''),
                text.casefold(),
            ),
        )

    def refresh_share_folder(self, snapshot_path: Path | None = None) -> Path:
        SHARE.mkdir(parents=True, exist_ok=True)

        targets = {
            'Reasoning Dataset.jsonl': self.dataset_path,
            'Session State.json': self.path,
            'Diagnostic Log.jsonl': LOG.path,
        }
        if snapshot_path and snapshot_path.exists():
            targets['Training Snapshot.jsonl'] = snapshot_path

        for name in (
            'Reasoning Dataset.jsonl',
            'Session State.json',
            'Diagnostic Log.jsonl',
            'Training Snapshot.jsonl',
        ):
            target = SHARE / name
            try:
                if target.exists():
                    target.unlink()
            except Exception:
                pass

        for name, source in targets.items():
            try:
                source = Path(source)
                if source.exists():
                    shutil.copy2(source, SHARE / name)
            except Exception as exc:
                LOG.error(
                    'share_file_copy_error',
                    exc,
                    source=str(source),
                    destination=str(SHARE / name),
                )

        LOG.event(
            'share_folder_refreshed',
            session_id=self.session_id,
            share_dir=str(SHARE),
            files=sorted(path.name for path in SHARE.iterdir() if path.is_file()),
        )
        return SHARE

    def export_training_snapshot(self) -> Path:
        stamp = time.strftime('%Y-%m-%d-%H-%M-%S')
        path = EXPORTS / f'Coverage Atlas Training Snapshot {self.session_id} {stamp}.jsonl'
        self._rewrite_dataset()
        shutil.copy2(self.dataset_path, path)

        self.data.setdefault('rounds', []).append({
            'type': 'training_snapshot',
            'at': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'historical_answered_count': self.answered_count,
            'round_answered_count': self.round_answered_count,
            'round_pair_count': self.total_pairs,
            'snapshot_path': str(path),
        })
        self.save()
        self._rewrite_dataset()
        self.refresh_share_folder(path)

        LOG.event(
            'training_snapshot_exported',
            session_id=self.session_id,
            historical_answered_count=self.answered_count,
            round_answered_count=self.round_answered_count,
            round_pair_count=self.total_pairs,
            snapshot_path=str(path),
            share_dir=str(SHARE),
        )
        return path

    @property
    def total_pairs(self) -> int:
        return len(self.data.get('pairs', []))

    @property
    def answered_count(self) -> int:
        return len(self.answers)

    @property
    def round_answered_count(self) -> int:
        return sum(
            1
            for left, right in self.data.get('pairs', [])
            if pair_id(left, right) in self.answers
        )

    @property
    def remaining_pair_count(self) -> int:
        return max(0, self.total_pairs - self.round_answered_count)

    def apply_rule_pack(self, rule_pack: SelectionRulePack):
        all_pairs = build_meaningful_pairs(self.releases)
        unresolved = []
        covered = {}
        previously_answered = 0

        for left_id, right_id in all_pairs:
            pid = pair_id(left_id, right_id)

            if pid in self.answers:
                previously_answered += 1
                continue

            left = self.releases[left_id]
            right = self.releases[right_id]
            evaluation = rule_pack.evaluate(left, right)

            if evaluation and evaluation['choice'] in {'left', 'right', 'both', 'skip'}:
                covered[pid] = {
                    'pair_id': pid,
                    'left_release_id': left_id,
                    'right_release_id': right_id,
                    'rule_id': evaluation['rule_id'],
                    'rule_index': evaluation['rule_index'],
                    'description': evaluation['description'],
                    'when': evaluation['when'],
                    'choice': evaluation['choice'],
                    'context': evaluation['context'],
                }
            else:
                unresolved.append([left_id, right_id])

        seed_text = f'{self.session_id}|{rule_pack.hash}|{len(self.data.get("rounds", []))}'
        seed = int(hashlib.sha1(seed_text.encode('utf-8')).hexdigest()[:12], 16)
        rng = random.Random(seed)
        rng.shuffle(unresolved)
        for pair in unresolved:
            if rng.random() < 0.5:
                pair.reverse()

        self.data['pairs'] = unresolved
        self.data['rule_evaluations'] = covered
        self.data['active_rule_pack'] = {
            'name': rule_pack.name,
            'version': rule_pack.version,
            'hash': rule_pack.hash,
            'source_path': str(rule_pack.source_path),
            'rule_count': len(rule_pack.rules),
        }
        self.data.setdefault('rounds', []).append({
            'type': 'rule_gap_training',
            'started_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'rule_pack': self.data['active_rule_pack'],
            'meaningful_pair_count': len(all_pairs),
            'previously_answered_count': previously_answered,
            'rule_covered_count': len(covered),
            'unresolved_pair_count': len(unresolved),
        })

        self.save()
        self._rewrite_dataset()

        LOG.event(
            'selection_rules_applied',
            session_id=self.session_id,
            rule_pack=self.data['active_rule_pack'],
            meaningful_pair_count=len(all_pairs),
            previously_answered_count=previously_answered,
            rule_covered_count=len(covered),
            unresolved_pair_count=len(unresolved),
        )

        return {
            'meaningful': len(all_pairs),
            'answered': previously_answered,
            'covered': len(covered),
            'unresolved': len(unresolved),
        }

    def next_pair(self):
        for left, right in self.data.get('pairs', []):
            pid = pair_id(left, right)
            if pid not in self.answers:
                return pid, left, right
        return None

    def save(self):
        self.data['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%S')
        temp = self.path.with_suffix('.json.tmp')
        temp.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
        temp.replace(self.path)

    def answer(
        self,
        pid: str,
        left: Release,
        right: Release,
        choice: str,
        reason: str,
        motivation: dict | None = None,
    ):
        if pid in self.answers:
            raise RuntimeError('This pair has already been answered.')

        winner = None
        loser = None
        kept_release_ids = []
        discarded_release_ids = []

        if choice == 'left':
            winner, loser = left.rid, right.rid
            kept_release_ids = [left.rid]
            discarded_release_ids = [right.rid]
        elif choice == 'right':
            winner, loser = right.rid, left.rid
            kept_release_ids = [right.rid]
            discarded_release_ids = [left.rid]
        elif choice == 'both':
            kept_release_ids = [left.rid, right.rid]

        row = {
            'pair_id': pid,
            'answered_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'choice': choice,
            'winner_release_id': winner,
            'loser_release_id': loser,
            'kept_release_ids': kept_release_ids,
            'discarded_release_ids': discarded_release_ids,
            'reason': reason.strip(),
            'motivation': motivation or {},
            'left_release_note': self.get_release_note(left.rid),
            'right_release_note': self.get_release_note(right.rid),
            'left_release': left.feature_snapshot(),
            'right_release': right.feature_snapshot(),
            'comparison': comparison_snapshot(left, right),
        }
        self.answers[pid] = row
        self.save()
        self._rewrite_dataset()

        LOG.event(
            'pair_answered',
            session_id=self.session_id,
            **row,
            historical_answered_count=self.answered_count,
            round_answered_count=self.round_answered_count,
            round_pair_count=self.total_pairs,
        )

    def _rewrite_dataset(self):
        try:
            with self.dataset_path.open('w', encoding='utf-8') as fh:
                header = {
                    'record_type': 'session',
                    'schema': 3,
                    'app': APP,
                    'version': VERSION,
                    'session_id': self.session_id,
                    'root': str(self.root),
                    'release_set_hash': self.release_hash,
                    'release_count': len(self.releases),
                    'round_pair_count': self.total_pairs,
                    'round_answered_count': self.round_answered_count,
                    'historical_answered_count': self.answered_count,
                    'pair_pool': 'meaningful_only',
                    'unanswered_pair_count': self.remaining_pair_count,
                }
                fh.write(json.dumps(header, ensure_ascii=False) + '\n')

                for release_id in sorted(self.releases):
                    note_text = self.get_release_note(release_id)
                    if not note_text:
                        continue
                    note_data = self.release_notes.get(release_id, {})
                    fh.write(json.dumps({
                        'record_type': 'release_note',
                        'release_id': release_id,
                        'note': note_text,
                        'updated_at': (
                            note_data.get('updated_at', '')
                            if isinstance(note_data, dict)
                            else ''
                        ),
                        'release': self.releases[release_id].feature_snapshot(),
                    }, ensure_ascii=False) + '\n')

                for evaluation in self.data.get('rule_evaluations', {}).values():
                    row = {'record_type': 'rule_evaluation'}
                    row.update(evaluation)
                    fh.write(json.dumps(row, ensure_ascii=False) + '\n')

                for answer in self.answers.values():
                    if answer:
                        row = {'record_type': 'comparison'}
                        row.update(answer)
                        fh.write(json.dumps(row, ensure_ascii=False) + '\n')
        except Exception as exc:
            LOG.error('dataset_export_error', exc, dataset_path=str(self.dataset_path))


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.releases: dict[str, Release] = {}
        self.session: Session | None = None
        self.current = None
        self.root_folder: Path | None = None
        self.scan_thread = None
        self.note_save_jobs = {'left': None, 'right': None}
        self.last_text_target = None
        self.answer_library_values = []

        self.title(f'{APP} {VERSION} - {STATUS}')
        self.geometry('1500x900')
        self.minsize(1150, 700)
        self.configure(bg='#14171b')
        self._style()
        self._build()
        self.protocol('WM_DELETE_WINDOW', self.on_close)

    def _style(self):
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure(
            '.',
            background='#1b1f24',
            foreground='#e8eaed',
            fieldbackground='#242a31',
            font=('Segoe UI', 10),
        )
        style.configure('TFrame', background='#14171b')
        style.configure('Panel.TFrame', background='#1b1f24')
        style.configure('TLabel', background='#14171b', foreground='#e8eaed')
        style.configure('Panel.TLabel', background='#1b1f24', foreground='#e8eaed')
        style.configure('Title.TLabel', background='#14171b', foreground='#ffffff', font=('Segoe UI Semibold', 18))
        style.configure('Release.TLabel', background='#1b1f24', foreground='#ffffff', font=('Segoe UI Semibold', 15))
        style.configure('Meta.TLabel', background='#1b1f24', foreground='#b8c0c8')
        style.configure('TButton', padding=(10, 7))
        style.configure('Accent.TButton', font=('Segoe UI Semibold', 10), padding=(12, 9))

    def _build(self):
        top = ttk.Frame(self)
        top.pack(fill='x', padx=14, pady=(12, 8))

        ttk.Label(top, text='Coverage Atlas Reasoning Lab', style='Title.TLabel').pack(side='left')
        ttk.Label(top, text=f'{VERSION} - {STATUS}').pack(side='left', padx=12)

        ttk.Button(top, text='Open files to send', command=self.open_share_folder).pack(side='right')
        self.snapshot_button = ttk.Button(
            top,
            text='Finish round / export snapshot',
            command=self.export_snapshot,
            state='disabled',
        )
        self.snapshot_button.pack(side='right', padx=(0, 8))
        self.rules_button = ttk.Button(
            top,
            text='Load selection rules',
            command=self.load_selection_rules,
        )
        self.rules_button.pack(side='right', padx=(0, 8))
        ttk.Button(top, text='Import test folder', command=self.choose_folder).pack(side='right', padx=(0, 8))

        info = ttk.Frame(self)
        info.pack(fill='x', padx=14, pady=(0, 8))
        self.folder_label = ttk.Label(info, text='No test folder loaded.')
        self.folder_label.pack(side='left')
        self.progress_label = ttk.Label(info, text='0 / 0 pairs')
        self.progress_label.pack(side='right')
        self.rules_label = ttk.Label(info, text='No selection rules loaded')
        self.rules_label.pack(side='right', padx=(0, 18))

        body = ttk.Panedwindow(self, orient='horizontal')
        body.pack(fill='both', expand=True, padx=14, pady=(0, 10))

        self.left_panel = ttk.Frame(body, style='Panel.TFrame')
        self.right_panel = ttk.Frame(body, style='Panel.TFrame')
        body.add(self.left_panel, weight=1)
        body.add(self.right_panel, weight=1)

        self.left_title = ttk.Label(self.left_panel, text='Release A', style='Release.TLabel')
        self.left_title.pack(anchor='w', padx=12, pady=(12, 4))
        self.left_meta = ttk.Label(self.left_panel, text='', style='Meta.TLabel', justify='left')
        self.left_meta.pack(anchor='w', padx=12, pady=(0, 2))
        self.left_folder_row = ttk.Frame(self.left_panel, style='Panel.TFrame')
        self.left_folder_row.pack(fill='x', padx=12, pady=(0, 8))
        self.left_folder_label = ttk.Label(
            self.left_folder_row,
            text='',
            style='Meta.TLabel',
            justify='left',
        )
        self.left_folder_label.pack(side='left', fill='x', expand=True)
        self.left_folder_button = ttk.Button(
            self.left_folder_row,
            text='📁',
            width=3,
            command=lambda: self.open_release_folder('left'),
            state='disabled',
        )
        self.left_folder_button.pack(side='right', padx=(8, 0))
        self.left_tracks = tk.Text(
            self.left_panel,
            bg='#101317',
            fg='#e8eaed',
            insertbackground='white',
            relief='flat',
            wrap='none',
            font=('Consolas', 10),
            padx=10,
            pady=10,
        )
        self.left_tracks.pack(fill='both', expand=True, padx=10, pady=(0, 6))

        ttk.Label(
            self.left_panel,
            text='Release A comment',
            style='Panel.TLabel',
        ).pack(anchor='w', padx=12, pady=(0, 3))
        self.left_note = tk.Text(
            self.left_panel,
            height=3,
            bg='#101317',
            fg='#e8eaed',
            insertbackground='white',
            relief='flat',
            wrap='word',
            font=('Segoe UI', 10),
            padx=8,
            pady=6,
        )
        self.left_note.pack(fill='x', padx=10, pady=(0, 8))
        self.left_note.bind('<KeyRelease>', lambda _event: self.queue_release_note_save('left'))
        self.left_note.bind('<FocusOut>', lambda _event: self.save_release_note('left'))

        self.left_button_row = ttk.Frame(self.left_panel, style='Panel.TFrame')
        self.left_button_row.pack(fill='x', padx=10, pady=(0, 12))
        self.left_button = ttk.Button(
            self.left_button_row,
            text='Choose Release A',
            style='Accent.TButton',
            command=lambda: self.answer('left'),
            state='disabled',
        )
        self.left_button.pack(anchor='center')

        self.right_title = ttk.Label(self.right_panel, text='Release B', style='Release.TLabel')
        self.right_title.pack(anchor='w', padx=12, pady=(12, 4))
        self.right_meta = ttk.Label(self.right_panel, text='', style='Meta.TLabel', justify='left')
        self.right_meta.pack(anchor='w', padx=12, pady=(0, 2))
        self.right_folder_row = ttk.Frame(self.right_panel, style='Panel.TFrame')
        self.right_folder_row.pack(fill='x', padx=12, pady=(0, 8))
        self.right_folder_label = ttk.Label(
            self.right_folder_row,
            text='',
            style='Meta.TLabel',
            justify='left',
        )
        self.right_folder_label.pack(side='left', fill='x', expand=True)
        self.right_folder_button = ttk.Button(
            self.right_folder_row,
            text='📁',
            width=3,
            command=lambda: self.open_release_folder('right'),
            state='disabled',
        )
        self.right_folder_button.pack(side='right', padx=(8, 0))
        self.right_tracks = tk.Text(
            self.right_panel,
            bg='#101317',
            fg='#e8eaed',
            insertbackground='white',
            relief='flat',
            wrap='none',
            font=('Consolas', 10),
            padx=10,
            pady=10,
        )
        self.right_tracks.pack(fill='both', expand=True, padx=10, pady=(0, 6))

        ttk.Label(
            self.right_panel,
            text='Release B comment',
            style='Panel.TLabel',
        ).pack(anchor='w', padx=12, pady=(0, 3))
        self.right_note = tk.Text(
            self.right_panel,
            height=3,
            bg='#101317',
            fg='#e8eaed',
            insertbackground='white',
            relief='flat',
            wrap='word',
            font=('Segoe UI', 10),
            padx=8,
            pady=6,
        )
        self.right_note.pack(fill='x', padx=10, pady=(0, 8))
        self.right_note.bind('<KeyRelease>', lambda _event: self.queue_release_note_save('right'))
        self.right_note.bind('<FocusOut>', lambda _event: self.save_release_note('right'))

        self.right_button_row = ttk.Frame(self.right_panel, style='Panel.TFrame')
        self.right_button_row.pack(fill='x', padx=10, pady=(0, 12))
        self.right_button = ttk.Button(
            self.right_button_row,
            text='Choose Release B',
            style='Accent.TButton',
            command=lambda: self.answer('right'),
            state='disabled',
        )
        self.right_button.pack(anchor='center')

        self.reason = tk.Text(self)
        self.reason.pack_forget()

        self.left_note.bind('<FocusIn>', lambda _event: self.set_text_target(self.left_note))
        self.right_note.bind('<FocusIn>', lambda _event: self.set_text_target(self.right_note))
        self.last_text_target = self.left_note

        library_frame = ttk.Frame(self)
        library_frame.pack(fill='x', padx=14, pady=(0, 8))
        ttk.Label(
            library_frame,
            text='Saved answers/comments - click to insert into the active text field:',
        ).pack(anchor='w')
        self.answer_library = tk.Listbox(
            library_frame,
            height=4,
            bg='#101317',
            fg='#e8eaed',
            selectbackground='#34495e',
            selectforeground='#ffffff',
            relief='flat',
            font=('Segoe UI', 10),
            activestyle='none',
            exportselection=False,
        )
        self.answer_library.pack(fill='x', pady=(4, 0))
        self.answer_library.bind('<<ListboxSelect>>', self.insert_saved_answer)

        actions = ttk.Frame(self)
        actions.pack(fill='x', padx=14, pady=(0, 8))

        center_actions = ttk.Frame(actions)
        center_actions.pack(anchor='center')

        self.keep_both_button = ttk.Button(
            center_actions,
            text='Keep Both',
            style='Accent.TButton',
            command=lambda: self.answer('both'),
            state='disabled',
        )
        self.keep_both_button.pack(side='left', padx=(0, 8))

        self.skip_button = ttk.Button(
            center_actions,
            text='Skip comparison',
            command=lambda: self.answer('skip'),
            state='disabled',
        )
        self.skip_button.pack(side='left')

        self.dataset_label = ttk.Label(actions, text='')
        self.dataset_label.pack(side='right')

        self.status = ttk.Label(self, text='Ready')
        self.status.pack(fill='x', padx=14, pady=(0, 10))

    def choose_folder(self):
        folder = filedialog.askdirectory(title='Select test music folder')
        if not folder:
            return

        self.root_folder = Path(folder)
        self.folder_label.config(text=str(self.root_folder))
        self.status.config(text='Scanning test folder...')
        self._set_buttons(False)

        def run():
            try:
                releases = scan_releases(
                    self.root_folder,
                    lambda done, total: self.after(
                        0,
                        lambda d=done, t=total: self.status.config(
                            text=f'Scanning audio files {d:,}/{t:,}...'
                        ),
                    ),
                )
                self.after(0, lambda: self.scan_finished(releases))
            except Exception as exc:
                LOG.error('scan_fatal_error', exc, root=str(self.root_folder))
                self.after(
                    0,
                    lambda: messagebox.showerror(
                        APP,
                        f'Could not scan the test folder:\n\n{exc}\n\nSee the log for details.',
                    ),
                )

        self.scan_thread = threading.Thread(target=run, daemon=True)
        self.scan_thread.start()

    def scan_finished(self, releases: dict[str, Release]):
        self.releases = releases

        if len(releases) < 2:
            self.session = None
            self.current = None
            self._clear_pair()
            self.status.config(
                text=f'Only {len(releases)} release(s) found. At least 2 are required.'
            )
            return

        self.session = Session(self.root_folder, releases)
        self.snapshot_button.config(state='normal')
        self.dataset_label.config(text=f'Dataset: {self.session.dataset_path.name}')
        self.refresh_answer_library()

        if RULES_FILE.exists():
            try:
                rule_pack = SelectionRulePack.load(RULES_FILE)
                stats = self.session.apply_rule_pack(rule_pack)
                self.rules_label.config(
                    text=f'Rules: {rule_pack.name} v{rule_pack.version} ({stats["covered"]} covered / {stats["unresolved"]} unresolved)'
                )
                self.status.config(
                    text=(
                        f'{len(releases):,} releases loaded - '
                        f'{stats["unresolved"]:,} unresolved meaningful pairs'
                    )
                )
            except Exception as exc:
                LOG.error('selection_rules_auto_load_error', exc, rules_path=str(RULES_FILE))
                self.rules_label.config(text='Selection rules failed to load')
                self.status.config(
                    text=f'{len(releases):,} releases loaded - rules file could not be applied'
                )
        else:
            self.rules_label.config(text='No selection rules loaded')
            self.status.config(
                text=f'{len(releases):,} releases loaded - {self.session.total_pairs:,} meaningful pairs'
            )

        self.show_next_pair()

    def set_text_target(self, widget):
        self.last_text_target = widget

    def refresh_answer_library(self):
        if not hasattr(self, 'answer_library'):
            return

        self.answer_library.delete(0, 'end')
        self.answer_library_values = []

        if not self.session:
            return

        self.answer_library_values = self.session.reusable_texts()[:30]
        for text in self.answer_library_values:
            display = text.replace('\n', ' ').strip()
            if len(display) > 180:
                display = display[:177] + '...'
            self.answer_library.insert('end', display)

    def insert_saved_answer(self, _event=None):
        selection = self.answer_library.curselection()
        if not selection:
            return

        index = selection[0]
        if index >= len(self.answer_library_values):
            return

        text = self.answer_library_values[index]
        target = self.last_text_target or self.left_note

        try:
            existing = target.get('1.0', 'end').rstrip()
            if existing:
                target.insert('end', (' ' if not existing.endswith((' ', '\n')) else '') + text)
            else:
                target.insert('1.0', text)
            target.focus_set()
            target.see('end')
        finally:
            self.answer_library.selection_clear(0, 'end')

        if target is self.left_note:
            self.queue_release_note_save('left')
        elif target is self.right_note:
            self.queue_release_note_save('right')

    def load_selection_rules(self):
        source = filedialog.askopenfilename(
            title='Select Coverage Atlas selection rules',
            filetypes=[
                ('JSON selection rules', '*.json'),
                ('All files', '*.*'),
            ],
        )
        if not source:
            return

        try:
            rule_pack = SelectionRulePack.load(Path(source))
            shutil.copy2(source, RULES_FILE)
            rule_pack = SelectionRulePack.load(RULES_FILE)

            if self.session:
                self.save_current_release_notes()
                stats = self.session.apply_rule_pack(rule_pack)
                self.rules_label.config(
                    text=f'Rules: {rule_pack.name} v{rule_pack.version} ({stats["covered"]} covered / {stats["unresolved"]} unresolved)'
                )
                self.refresh_answer_library()
                self.show_next_pair()
                messagebox.showinfo(
                    APP,
                    'Selection rules applied.\n\n'
                    f'Meaningful pairs: {stats["meaningful"]}\n'
                    f'Already answered: {stats["answered"]}\n'
                    f'Covered by rules: {stats["covered"]}\n'
                    f'Unresolved for the next round: {stats["unresolved"]}',
                )
            else:
                self.rules_label.config(text=f'Rules: {rule_pack.name} v{rule_pack.version} ({len(rule_pack.rules)} rules)')
                messagebox.showinfo(
                    APP,
                    'Selection rules saved.\n\n'
                    'Import the same test folder and only unresolved cases will be queued.',
                )
        except Exception as exc:
            LOG.error('selection_rules_load_error', exc, source=str(source))
            messagebox.showerror(
                APP,
                f'Could not load the selection rules:\n\n{exc}',
            )

    def export_snapshot(self):
        if not self.session:
            return

        self.save_current_release_notes()

        try:
            path = self.session.export_training_snapshot()
            self.refresh_answer_library()
            messagebox.showinfo(
                APP,
                'Training snapshot saved.\n\n'
                'The exact files to send are now collected in:\n'
                f'{SHARE}',
            )
        except Exception as exc:
            LOG.error('snapshot_export_error', exc)
            messagebox.showerror(APP, f'Could not export the training snapshot:\n\n{exc}')

    def _set_buttons(self, enabled: bool):
        state = 'normal' if enabled else 'disabled'
        self.left_button.config(state=state)
        self.keep_both_button.config(state=state)
        self.skip_button.config(state=state)
        self.right_button.config(state=state)

    def _clear_pair(self):
        self.left_title.config(text='Release A')
        self.right_title.config(text='Release B')
        self.left_meta.config(text='')
        self.right_meta.config(text='')
        self.left_folder_label.config(text='')
        self.right_folder_label.config(text='')
        self.left_folder_button.config(state='disabled')
        self.right_folder_button.config(state='disabled')
        for box in (self.left_tracks, self.right_tracks):
            box.config(state='normal')
            box.delete('1.0', 'end')
            box.config(state='disabled')
        for note_box in (self.left_note, self.right_note):
            note_box.delete('1.0', 'end')
        self._set_buttons(False)

    def current_release_for_side(self, side: str):
        if not self.current:
            return None
        _, left, right = self.current
        return left if side == 'left' else right

    def note_widget_for_side(self, side: str):
        return self.left_note if side == 'left' else self.right_note

    def queue_release_note_save(self, side: str):
        job = self.note_save_jobs.get(side)
        if job:
            try:
                self.after_cancel(job)
            except Exception:
                pass
        self.note_save_jobs[side] = self.after(
            500,
            lambda s=side: self.save_release_note(s),
        )

    def save_release_note(self, side: str):
        job = self.note_save_jobs.get(side)
        if job:
            try:
                self.after_cancel(job)
            except Exception:
                pass
            self.note_save_jobs[side] = None

        if not self.session:
            return

        release = self.current_release_for_side(side)
        if not release:
            return

        widget = self.note_widget_for_side(side)
        text = widget.get('1.0', 'end').strip()

        try:
            self.session.set_release_note(release.rid, text)
            self.refresh_answer_library()
        except Exception as exc:
            LOG.error(
                'release_note_save_error',
                exc,
                release_id=release.rid,
                side=side,
            )

    def save_current_release_notes(self):
        self.save_release_note('left')
        self.save_release_note('right')

    def release_meta_text(self, release: Release) -> str:
        snap = release.feature_snapshot()
        size_gib = snap['total_size_bytes'] / (1024 ** 3)
        return (
            f"{release.date or '-'} | {release.media_type} ({release.media_reason})\n"
            f"Album artist: {release.albumartist or '-'}\n"
            f"Barcode: {release.barcode or '-'}\n"
            f"MusicBrainz release: {release.mbrel or '-'}\n"
            f"Tracks: {snap['track_count']} | Discs: {snap['disc_count']} | "
            f"Duration: {snap['total_duration_sec'] / 60:.1f} min | Size: {size_gib:.2f} GiB\n"
            f"Formats: {', '.join(f'{k}:{v}' for k, v in snap['extensions'].items()) or '-'}\n"
            f"Logchecker: {release.logchecker_score if release.logchecker_score is not None else ('unavailable' if release.logchecker_logs else 'no rip log')}"
        )

    def track_text(self, release: Release, other: Release, side: str) -> str:
        other_ids = {semantic_identity(track) for track in other.tracks}
        lines = []
        for index, track in enumerate(release.tracks, 1):
            shared = semantic_identity(track) in other_ids
            mark = '=' if shared else side
            duration = f'{track.duration / 60:.2f}' if track.duration else '-'
            identity = (
                f'ISRC:{track.isrc}' if track.isrc else
                f'MB:{track.mbrec}' if track.mbrec else
                'metadata'
            )
            selection_class = track_selection_class(track)
            suffix = ''
            if selection_class == 'ignored_live':
                suffix = ' [ignored: live]'
            elif selection_class == 'ignored_remix':
                suffix = ' [ignored: remix]'
            elif selection_class == 'ignored_snippet':
                suffix = ' [ignored: snippet]'
            elif selection_class == 'valuable_version':
                suffix = ' [counts: version]'
            elif selection_class == 'valuable_featured_remix':
                suffix = ' [counts: featured remix]'

            lines.append(
                f'[{mark}] {index:02d}. {track.artist} - {track.title} '
                f'[{duration}] [{track.extension or "-"}] [{identity}]{suffix}'
            )
        return '\n'.join(lines)

    def show_next_pair(self):
        if not self.session:
            return

        nxt = self.session.next_pair()
        self.progress_label.config(
            text=f'{self.session.round_answered_count:,} / {self.session.total_pairs:,} meaningful pairs'
        )

        if not nxt:
            self.current = None
            self._clear_pair()
            self.status.config(
                text='No unresolved pairs remain in this round. Export the snapshot; the selection system currently covers every remaining meaningful case.'
            )
            LOG.event(
                'session_complete',
                session_id=self.session.session_id,
                round_pair_count=self.session.total_pairs,
                historical_answered_count=self.session.answered_count,
                dataset_path=str(self.session.dataset_path),
            )
            return

        pid, left_id, right_id = nxt
        left = self.releases[left_id]
        right = self.releases[right_id]
        self.current = (pid, left, right)

        self.left_title.config(text=left.name)
        self.right_title.config(text=right.name)
        self.left_meta.config(text=self.release_meta_text(left))
        self.right_meta.config(text=self.release_meta_text(right))
        self.left_folder_label.config(text=f'Folder: {left.relative_folder}')
        self.right_folder_label.config(text=f'Folder: {right.relative_folder}')
        self.left_folder_button.config(state='normal')
        self.right_folder_button.config(state='normal')

        left_text = self.track_text(left, right, 'A')
        right_text = self.track_text(right, left, 'B')

        self.left_tracks.config(state='normal')
        self.left_tracks.delete('1.0', 'end')
        self.left_tracks.insert('1.0', left_text)
        self.left_tracks.config(state='disabled')

        self.right_tracks.config(state='normal')
        self.right_tracks.delete('1.0', 'end')
        self.right_tracks.insert('1.0', right_text)
        self.right_tracks.config(state='disabled')

        self.left_note.delete('1.0', 'end')
        self.left_note.insert('1.0', self.session.get_release_note(left.rid))
        self.right_note.delete('1.0', 'end')
        self.right_note.insert('1.0', self.session.get_release_note(right.rid))

        self._set_buttons(True)

        compare = comparison_snapshot(left, right)
        self.status.config(
            text=(
                f"Shared: {compare['shared_identity_count']} | "
                f"A-only: {compare['left_only_identity_count']} | "
                f"B-only: {compare['right_only_identity_count']}"
            )
        )
        LOG.event(
            'pair_presented',
            session_id=self.session.session_id,
            pair_id=pid,
            left_release=left.feature_snapshot(),
            right_release=right.feature_snapshot(),
            comparison=compare,
            answered_count=self.session.answered_count,
            total_pairs=self.session.total_pairs,
        )

    def ask_choice_motivation(
        self,
        chosen: Release,
        other: Release,
        inference: dict,
    ):
        result = {'text': None}
        dialog = tk.Toplevel(self)
        dialog.title('What motivated this choice?')
        dialog.configure(bg='#14171b')
        dialog.transient(self)
        dialog.grab_set()
        dialog.geometry('760x560')
        dialog.minsize(620, 460)

        outer = ttk.Frame(dialog)
        outer.pack(fill='both', expand=True, padx=14, pady=14)

        ttk.Label(
            outer,
            text='I could not infer this choice confidently.',
            style='Release.TLabel',
        ).pack(anchor='w')

        ttk.Label(
            outer,
            text=(
                f'Chosen: {chosen.name}\n'
                f'Other: {other.name}'
            ),
            style='Meta.TLabel',
            justify='left',
        ).pack(anchor='w', pady=(4, 10))

        if inference.get('counter_evidence'):
            ttk.Label(
                outer,
                text='Why I am unsure:',
                style='Panel.TLabel',
            ).pack(anchor='w')
            for line in inference['counter_evidence']:
                ttk.Label(
                    outer,
                    text='- ' + str(line),
                    style='Meta.TLabel',
                    wraplength=700,
                    justify='left',
                ).pack(anchor='w', pady=(1, 0))

        candidates = inference.get('candidates') or []
        if candidates:
            ttk.Label(
                outer,
                text='Possible explanation - click one to start:',
                style='Panel.TLabel',
            ).pack(anchor='w', pady=(10, 3))

        text_box = tk.Text(
            outer,
            height=6,
            bg='#101317',
            fg='#e8eaed',
            insertbackground='white',
            relief='flat',
            wrap='word',
            font=('Segoe UI', 10),
            padx=10,
            pady=8,
        )

        def insert_text(value: str):
            current = text_box.get('1.0', 'end').strip()
            if current:
                text_box.insert('end', (' ' if not current.endswith((' ', '\n')) else '') + value)
            else:
                text_box.insert('1.0', value)
            text_box.focus_set()
            text_box.see('end')

        for item in candidates[:5]:
            ttk.Button(
                outer,
                text=item['summary'],
                command=lambda value=item['summary']: insert_text(value),
            ).pack(fill='x', pady=2)

        if self.session:
            reusable = self.session.reusable_texts()[:8]
            if reusable:
                ttk.Label(
                    outer,
                    text='Saved motivations/comments:',
                    style='Panel.TLabel',
                ).pack(anchor='w', pady=(10, 3))
                saved = tk.Listbox(
                    outer,
                    height=min(5, len(reusable)),
                    bg='#101317',
                    fg='#e8eaed',
                    selectbackground='#34495e',
                    selectforeground='#ffffff',
                    relief='flat',
                    font=('Segoe UI', 10),
                    activestyle='none',
                    exportselection=False,
                )
                saved.pack(fill='x')
                for value in reusable:
                    display = value.replace('\n', ' ').strip()
                    if len(display) > 150:
                        display = display[:147] + '...'
                    saved.insert('end', display)

                def use_saved(_event=None):
                    selection = saved.curselection()
                    if selection:
                        insert_text(reusable[selection[0]])
                        saved.selection_clear(0, 'end')

                saved.bind('<<ListboxSelect>>', use_saved)

        ttk.Label(
            outer,
            text='Your motivation:',
            style='Panel.TLabel',
        ).pack(anchor='w', pady=(10, 3))
        text_box.pack(fill='both', expand=True)

        buttons = ttk.Frame(outer)
        buttons.pack(fill='x', pady=(10, 0))

        def save():
            result['text'] = text_box.get('1.0', 'end').strip()
            dialog.destroy()

        def save_unspecified():
            result['text'] = ''
            dialog.destroy()

        ttk.Button(
            buttons,
            text='Save motivation',
            style='Accent.TButton',
            command=save,
        ).pack(side='left')
        ttk.Button(
            buttons,
            text='No specific reason',
            command=save_unspecified,
        ).pack(side='left', padx=8)
        ttk.Button(
            buttons,
            text='Cancel choice',
            command=dialog.destroy,
        ).pack(side='right')

        dialog.protocol('WM_DELETE_WINDOW', dialog.destroy)
        text_box.focus_set()
        self.wait_window(dialog)
        return result['text']

    def ask_keep_both_motivation(self, left: Release, right: Release):
        result = {'text': None}

        dialog = tk.Toplevel(self)
        dialog.title('Why keep both?')
        dialog.configure(bg='#14171b')
        dialog.transient(self)
        dialog.grab_set()
        dialog.geometry('720x390')
        dialog.minsize(580, 340)

        outer = ttk.Frame(dialog)
        outer.pack(fill='both', expand=True, padx=14, pady=14)

        ttk.Label(
            outer,
            text='I cannot see valuable unique tracks on both sides.',
            style='Release.TLabel',
        ).pack(anchor='w')

        ttk.Label(
            outer,
            text=(
                f'Release A: {left.name}\n'
                f'Release B: {right.name}\n\n'
                'Why should both releases be kept?'
            ),
            style='Meta.TLabel',
            justify='left',
            wraplength=680,
        ).pack(anchor='w', pady=(6, 10))

        box = tk.Text(
            outer,
            height=8,
            bg='#101317',
            fg='#e8eaed',
            insertbackground='white',
            relief='flat',
            wrap='word',
            font=('Segoe UI', 10),
            padx=10,
            pady=8,
        )
        box.pack(fill='both', expand=True)

        buttons = ttk.Frame(outer)
        buttons.pack(fill='x', pady=(10, 0))

        def save():
            result['text'] = box.get('1.0', 'end').strip()
            dialog.destroy()

        ttk.Button(
            buttons,
            text='Keep Both',
            style='Accent.TButton',
            command=save,
        ).pack(side='left')

        ttk.Button(
            buttons,
            text='Cancel',
            command=dialog.destroy,
        ).pack(side='right')

        dialog.protocol('WM_DELETE_WINDOW', dialog.destroy)
        box.focus_set()
        self.wait_window(dialog)
        return result['text']

    def answer(self, choice: str):
        if not self.session or not self.current:
            return

        pid, left, right = self.current
        self.save_current_release_notes()

        reason = ''
        motivation = {
            'mode': 'skip',
            'confidence': 1.0,
            'categories': [],
            'summary': '',
        }

        if choice in {'left', 'right'}:
            chosen = left if choice == 'left' else right
            other = right if choice == 'left' else left
            motivation = infer_choice_motivation(
                chosen,
                other,
                self.session.answers,
            )

            if motivation.get('mode') == 'needs_user':
                user_text = self.ask_choice_motivation(
                    chosen,
                    other,
                    motivation,
                )
                if user_text is None:
                    return

                reason = user_text
                motivation['mode'] = 'user'
                motivation['user_text'] = user_text
                motivation['categories'] = sorted(
                    reason_categories(user_text)
                )
                motivation['summary'] = user_text
                motivation['confidence'] = 1.0 if user_text else 0.5
            else:
                reason = str(motivation.get('summary') or '')

        elif choice == 'both':
            motivation = infer_keep_both_motivation(left, right)

            if motivation.get('mode') == 'needs_user':
                user_text = self.ask_keep_both_motivation(left, right)
                if user_text is None:
                    return

                reason = user_text
                motivation['mode'] = 'user'
                motivation['user_text'] = user_text
                motivation['categories'] = ['keep_both']
                motivation['summary'] = user_text
                motivation['confidence'] = 1.0 if user_text else 0.5
            else:
                reason = str(motivation.get('summary') or '')

        try:
            self.session.answer(
                pid,
                left,
                right,
                choice,
                reason,
                motivation=motivation,
            )
        except Exception as exc:
            LOG.error('answer_save_error', exc, pair_id=pid)
            messagebox.showerror(APP, f'Could not save the answer:\n\n{exc}')
            return

        LOG.event(
            'choice_motivation_resolved',
            session_id=self.session.session_id,
            pair_id=pid,
            choice=choice,
            motivation=motivation,
        )

        self.refresh_answer_library()
        self.show_next_pair()

    def open_release_folder(self, side: str):
        release = self.current_release_for_side(side)
        if not release:
            return
        folder = Path(release.folder)
        try:
            if not folder.exists():
                raise FileNotFoundError(str(folder))
            if os.name == 'nt':
                os.startfile(str(folder))
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', str(folder)])
            else:
                subprocess.Popen(['xdg-open', str(folder)])
            LOG.event(
                'release_folder_opened',
                release_id=release.rid,
                release=release.name,
                folder=str(folder),
                side=side,
            )
        except Exception as exc:
            LOG.error(
                'release_folder_open_error',
                exc,
                release_id=release.rid,
                release=release.name,
                folder=str(folder),
                side=side,
            )
            messagebox.showerror(
                APP,
                f'Could not open the release folder:\n\n{folder}\n\n{exc}',
            )

    def open_share_folder(self):
        try:
            SHARE.mkdir(parents=True, exist_ok=True)

            if self.session:
                self.save_current_release_notes()
                self.session._rewrite_dataset()
                self.session.refresh_share_folder()

            if os.name == 'nt':
                os.startfile(str(SHARE))
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', str(SHARE)])
            else:
                subprocess.Popen(['xdg-open', str(SHARE)])
        except Exception as exc:
            LOG.error('open_share_folder_error', exc, share_dir=str(SHARE))
            messagebox.showerror(APP, f'Could not open the files-to-send folder:\n\n{exc}')

    def report_callback_exception(self, exc, value, tb):
        LOG.event(
            'ui_callback_error',
            error_type=getattr(exc, '__name__', str(exc)),
            error=str(value),
            traceback=''.join(traceback.format_exception(exc, value, tb)),
        )
        messagebox.showerror(
            APP,
            f'Unexpected error:\n\n{value}\n\nThe diagnostic log contains the traceback.',
        )

    def on_close(self):
        if self.session:
            self.save_current_release_notes()
            LOG.event(
                'session_paused',
                session_id=self.session.session_id,
                answered_count=self.session.answered_count,
                total_pairs=self.session.total_pairs,
                dataset_path=str(self.session.dataset_path),
            )
        LOG.close()
        self.destroy()


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
            traceback=''.join(
                traceback.format_exception(
                    args.exc_type,
                    args.exc_value,
                    args.exc_traceback,
                )
            ),
        )

    threading.excepthook = _thread_hook


if __name__ == '__main__':
    App().mainloop()
