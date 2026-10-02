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
import urllib.request
from pathlib import Path
from dataclasses import dataclass, field
from collections import defaultdict, Counter
from difflib import SequenceMatcher

APP = 'Coverage Atlas'
VERSION = '0.1.11'
STATUS = 'Under construction ⚠️'
MATCH_DURATION_TOLERANCE = 2.0
EXACT_SEARCH_RELEASE_LIMIT = 70


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
            html_out = DATA / 'temp' / f'logchecker-{token}.html'
            json_out = DATA / 'temp' / f'logchecker-{token}.json'
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


REMIX_RE = re.compile(r'(?<![a-z0-9])(?:remix(?:es|ed)?|rmx)(?![a-z0-9])', re.I)
LIVE_CONTEXT_RE = re.compile(
    r'(?:'
    r'[\(\[][^\)\]]*\blive\b[^\)\]]*[\)\]]'
    r'|\blive\s+(?:at|from|in|on)\b'
    r'|\brecorded\s+live\b'
    r'|\blive\s+version\b'
    r'|[-–—:]\s*live\b'
    r')',
    re.I,
)
LIVE_RELEASE_RE = re.compile(
    r'(?:^|[-–—:]\s*)live\s*$'
    r'|\blive\s+(?:at|from|in|on)\b'
    r'|\brecorded\s+live\b',
    re.I,
)


def ignored_track_reason(title: str, release_name: str, folder: str) -> str:
    title = title or ''
    release_name = release_name or ''
    folder_name = Path(folder).name if folder else ''

    if REMIX_RE.search(title):
        return 'remix'

    release_context = f'{release_name} {folder_name}'
    if REMIX_RE.search(release_context):
        return 'remix release'

    if LIVE_CONTEXT_RE.search(title):
        return 'live recording'

    if LIVE_CONTEXT_RE.search(release_context) or LIVE_RELEASE_RE.search(release_context.strip()):
        return 'live release'

    return ''


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


def logchecker_release_signature(release: Release) -> tuple:
    entries = []
    for track in release.tracks:
        identity = track.key
        if not identity:
            mbid = clean_identifier(track.mbrec)
            isrc = clean_identifier(track.isrc)
            identity = (
                'mb:' + mbid if mbid else
                'isrc:' + isrc if isrc else
                f'meta:{compact_text(track.artist)}:{compact_text(track.title)}'
            )
        entries.append((identity, int(round(track.duration)) if track.duration else 0))
    return tuple(sorted(entries))


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

    def scan(self, root: str, progress=lambda a, b: None):
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
                duration = float(getattr(getattr(media, 'info', None), 'length', 0) or 0)
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
                        groups[rid].name,
                        groups[rid].folder,
                    ),
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

        duplicate_rips = defaultdict(list)
        for release in self.releases.values():
            if release.kind == 'RELEASE' and release.media_type == 'CD' and release.tracks:
                duplicate_rips[logchecker_release_signature(release)].append(release)

        logchecker_scored = 0
        logchecker_candidate_count = 0
        for candidates in duplicate_rips.values():
            if len(candidates) < 2:
                continue
            logs_by_release = {}
            for release in candidates:
                try:
                    paths = sorted(
                        p for p in Path(release.folder).rglob('*.log')
                        if p.is_file() and not p.name.casefold().startswith('logchecker')
                    )
                except Exception as exc:
                    LOG.error('logchecker_log_discovery_failed', exc, folder=release.folder)
                    paths = []
                if paths:
                    logs_by_release[release.rid] = paths
            if len(logs_by_release) < 2:
                continue
            logchecker_candidate_count += len(logs_by_release)
            for release in candidates:
                paths = logs_by_release.get(release.rid)
                if not paths:
                    continue
                release.logchecker_score, release.logchecker_logs = score_release_logs(release.folder, paths)
                logchecker_scored += int(release.logchecker_score is not None)
                LOG.event(
                    'release_logchecker_summary',
                    release_id=release.rid,
                    release=release.name,
                    score=release.logchecker_score,
                    log_count=len(release.logchecker_logs),
                    logs=release.logchecker_logs,
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

    @staticmethod
    def media_rank(media_type: str) -> int:
        return 0 if media_type == 'CD' else 1

    def objective(self, selected: set[str]):
        media_penalty = sum(self.media_rank(self.releases[rid].media_type) for rid in selected)
        file_count = sum(len(self.releases[rid].tracks) for rid in selected)
        log_score_total = sum(
            float(self.releases[rid].logchecker_score)
            for rid in selected
            if self.releases[rid].logchecker_score is not None
        )
        stable = tuple(sorted((self.releases[rid].date, self.releases[rid].name, rid) for rid in selected))
        return (len(selected), media_penalty, file_count, -log_score_total, stable)

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
                return (
                    -len(release.keys & remaining),
                    self.media_rank(release.media_type),
                    len(release.tracks),
                    len(release.keys),
                    -(float(release.logchecker_score) if release.logchecker_score is not None else 0.0),
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
                    -self.media_rank(self.releases[rid].media_type),
                    -len(self.releases[rid].tracks),
                    len(self.releases[rid].keys),
                    (float(self.releases[rid].logchecker_score) if self.releases[rid].logchecker_score is not None else -1.0),
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
            best_media_penalty=best_obj[1],
            best_file_count=best_obj[2],
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
                cd_covered = bool(release.keys) and all(
                    any(
                        provider in self.kept and self.releases[provider].media_type == 'CD'
                        for provider in self.cover[key]
                    )
                    for key in release.keys
                )
                if release.media_type == 'WEB' and cd_covered:
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

        columns = ('status', 'date', 'release', 'media', 'source', 'tracks', 'unique', 'log_score', 'barcode')
        self.tree = ttk.Treeview(left, columns=columns, show='headings')
        for column, width in (
            ('status', 100), ('date', 95), ('release', 330), ('media', 75), ('source', 95),
            ('tracks', 65), ('unique', 70), ('log_score', 85), ('barcode', 135)
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
        path = filedialog.askdirectory(title='Select music collection')
        if not path:
            return
        self.rootpath = path
        LOG.event('user_analyze_folder', root_name=Path(path).name)
        self.status.config(text='Scanning...')

        def run():
            self.m.scan(
                path,
                lambda i, n: self.after(0, lambda: self.status.config(text=f'Scanning {i:,}/{n:,} files...')),
            )
            self.after(0, self.done)

        threading.Thread(target=run, daemon=True).start()

    def done(self):
        self.refresh()
        self.status.config(
            text=f'Analysis complete - {len(self.m.releases):,} releases - log: {LOG.path.name}'
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
            f'Logchecker score: {release.logchecker_score if release.logchecker_score is not None else ("unavailable" if release.logchecker_logs else "no rip log")}\n'
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
        except Exception:
            self.last = ''

    def save_settings(self):
        try:
            SETTINGS.write_text(
                json.dumps({'last_folder': getattr(self, 'rootpath', '')}, indent=2),
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
