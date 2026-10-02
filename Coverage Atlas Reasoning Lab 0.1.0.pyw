from __future__ import annotations

import os
import sys
import re
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
from pathlib import Path
from dataclasses import dataclass, field
from collections import Counter
from itertools import combinations

APP = 'Coverage Atlas Reasoning Lab'
VERSION = '0.1.0'
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
TEMP = DATA / 'temp'
DEPS = DATA / 'dependencies'
for folder in (LOGS, SESSIONS, EXPORTS, TEMP, DEPS):
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

    LOG.event(
        'scan_complete',
        root=str(root),
        release_count=len(groups),
        audio_file_count=len(files),
        file_failures=failures,
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
    left_ids = {track.identity() for track in left.tracks}
    right_ids = {track.identity() for track in right.tracks}
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
    }


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
                    LOG.event(
                        'session_resumed',
                        session_id=self.session_id,
                        answered_count=len(data.get('answers', {})),
                        total_pairs=len(data.get('pairs', [])),
                    )
                    return data
            except Exception as exc:
                LOG.error('session_load_error', exc, session_path=str(self.path))

        release_ids = sorted(releases for releases in self.releases)
        pairs = [list(pair) for pair in combinations(release_ids, 2)]
        seed = int(hashlib.sha1(self.session_id.encode('utf-8')).hexdigest()[:12], 16)
        rng = random.Random(seed)
        rng.shuffle(pairs)
        for pair in pairs:
            if rng.random() < 0.5:
                pair.reverse()

        data = {
            'schema': 1,
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
    def total_pairs(self) -> int:
        return len(self.data.get('pairs', []))

    @property
    def answered_count(self) -> int:
        return len(self.answers)

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

    def answer(self, pid: str, left: Release, right: Release, choice: str, reason: str):
        if pid in self.answers:
            raise RuntimeError('This pair has already been answered.')

        winner = None
        loser = None
        if choice == 'left':
            winner, loser = left.rid, right.rid
        elif choice == 'right':
            winner, loser = right.rid, left.rid

        row = {
            'pair_id': pid,
            'answered_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'choice': choice,
            'winner_release_id': winner,
            'loser_release_id': loser,
            'reason': reason.strip(),
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
            answered_count=self.answered_count,
            total_pairs=self.total_pairs,
        )

    def _rewrite_dataset(self):
        try:
            with self.dataset_path.open('w', encoding='utf-8') as fh:
                header = {
                    'record_type': 'session',
                    'schema': 1,
                    'app': APP,
                    'version': VERSION,
                    'session_id': self.session_id,
                    'root': str(self.root),
                    'release_set_hash': self.release_hash,
                    'release_count': len(self.releases),
                    'total_pairs': self.total_pairs,
                    'answered_count': self.answered_count,
                }
                fh.write(json.dumps(header, ensure_ascii=False) + '\n')
                for left, right in self.data.get('pairs', []):
                    pid = pair_id(left, right)
                    answer = self.answers.get(pid)
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

        ttk.Button(top, text='Open data folder', command=self.open_data_folder).pack(side='right')
        ttk.Button(top, text='Import test folder', command=self.choose_folder).pack(side='right', padx=(0, 8))

        info = ttk.Frame(self)
        info.pack(fill='x', padx=14, pady=(0, 8))
        self.folder_label = ttk.Label(info, text='No test folder loaded.')
        self.folder_label.pack(side='left')
        self.progress_label = ttk.Label(info, text='0 / 0 pairs')
        self.progress_label.pack(side='right')

        body = ttk.Panedwindow(self, orient='horizontal')
        body.pack(fill='both', expand=True, padx=14, pady=(0, 10))

        self.left_panel = ttk.Frame(body, style='Panel.TFrame')
        self.right_panel = ttk.Frame(body, style='Panel.TFrame')
        body.add(self.left_panel, weight=1)
        body.add(self.right_panel, weight=1)

        self.left_title = ttk.Label(self.left_panel, text='Release A', style='Release.TLabel')
        self.left_title.pack(anchor='w', padx=12, pady=(12, 4))
        self.left_meta = ttk.Label(self.left_panel, text='', style='Meta.TLabel', justify='left')
        self.left_meta.pack(anchor='w', padx=12, pady=(0, 8))
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
        self.left_tracks.pack(fill='both', expand=True, padx=10, pady=(0, 10))

        self.right_title = ttk.Label(self.right_panel, text='Release B', style='Release.TLabel')
        self.right_title.pack(anchor='w', padx=12, pady=(12, 4))
        self.right_meta = ttk.Label(self.right_panel, text='', style='Meta.TLabel', justify='left')
        self.right_meta.pack(anchor='w', padx=12, pady=(0, 8))
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
        self.right_tracks.pack(fill='both', expand=True, padx=10, pady=(0, 10))

        reason_frame = ttk.Frame(self)
        reason_frame.pack(fill='x', padx=14, pady=(0, 8))
        ttk.Label(reason_frame, text='Why do you prefer one release? (optional)').pack(anchor='w')
        self.reason = tk.Text(
            reason_frame,
            height=4,
            bg='#101317',
            fg='#e8eaed',
            insertbackground='white',
            relief='flat',
            wrap='word',
            font=('Segoe UI', 10),
            padx=10,
            pady=8,
        )
        self.reason.pack(fill='x', pady=(4, 0))

        actions = ttk.Frame(self)
        actions.pack(fill='x', padx=14, pady=(0, 8))
        self.left_button = ttk.Button(
            actions,
            text='Choose Release A',
            style='Accent.TButton',
            command=lambda: self.answer('left'),
            state='disabled',
        )
        self.left_button.pack(side='left')
        self.skip_button = ttk.Button(
            actions,
            text='Skip',
            command=lambda: self.answer('skip'),
            state='disabled',
        )
        self.skip_button.pack(side='left', padx=8)
        self.right_button = ttk.Button(
            actions,
            text='Choose Release B',
            style='Accent.TButton',
            command=lambda: self.answer('right'),
            state='disabled',
        )
        self.right_button.pack(side='left')
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
        self.dataset_label.config(text=f'Dataset: {self.session.dataset_path.name}')
        self.status.config(
            text=f'{len(releases):,} releases loaded - {self.session.total_pairs:,} unique pairs'
        )
        self.show_next_pair()

    def _set_buttons(self, enabled: bool):
        state = 'normal' if enabled else 'disabled'
        self.left_button.config(state=state)
        self.skip_button.config(state=state)
        self.right_button.config(state=state)

    def _clear_pair(self):
        self.left_title.config(text='Release A')
        self.right_title.config(text='Release B')
        self.left_meta.config(text='')
        self.right_meta.config(text='')
        for box in (self.left_tracks, self.right_tracks):
            box.config(state='normal')
            box.delete('1.0', 'end')
            box.config(state='disabled')
        self.reason.delete('1.0', 'end')
        self._set_buttons(False)

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
            f"Folder: {release.relative_folder}"
        )

    def track_text(self, release: Release, other: Release, side: str) -> str:
        other_ids = {track.identity() for track in other.tracks}
        lines = []
        for index, track in enumerate(release.tracks, 1):
            shared = track.identity() in other_ids
            mark = '=' if shared else side
            duration = f'{track.duration / 60:.2f}' if track.duration else '-'
            identity = (
                f'ISRC:{track.isrc}' if track.isrc else
                f'MB:{track.mbrec}' if track.mbrec else
                'metadata'
            )
            lines.append(
                f'[{mark}] {index:02d}. {track.artist} - {track.title} '
                f'[{duration}] [{track.extension or "-"}] [{identity}]'
            )
        return '\n'.join(lines)

    def show_next_pair(self):
        if not self.session:
            return

        nxt = self.session.next_pair()
        self.progress_label.config(
            text=f'{self.session.answered_count:,} / {self.session.total_pairs:,} pairs'
        )

        if not nxt:
            self.current = None
            self._clear_pair()
            self.status.config(
                text='All release pairs are complete. Upload the exported reasoning dataset to me and I can derive the rules.'
            )
            LOG.event(
                'session_complete',
                session_id=self.session.session_id,
                total_pairs=self.session.total_pairs,
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

        self.reason.delete('1.0', 'end')
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

    def answer(self, choice: str):
        if not self.session or not self.current:
            return

        pid, left, right = self.current
        reason = self.reason.get('1.0', 'end').strip()

        try:
            self.session.answer(pid, left, right, choice, reason)
        except Exception as exc:
            LOG.error('answer_save_error', exc, pair_id=pid)
            messagebox.showerror(APP, f'Could not save the answer:\n\n{exc}')
            return

        self.show_next_pair()

    def open_data_folder(self):
        try:
            if os.name == 'nt':
                os.startfile(str(DATA))
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', str(DATA)])
            else:
                subprocess.Popen(['xdg-open', str(DATA)])
        except Exception as exc:
            LOG.error('open_data_folder_error', exc)
            messagebox.showerror(APP, f'Could not open the data folder:\n\n{exc}')

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
