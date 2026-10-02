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
from pathlib import Path
from dataclasses import dataclass, field
from collections import Counter
from itertools import combinations

APP = 'Coverage Atlas Reasoning Lab'
VERSION = '0.1.4'
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


def meaningful_pair(left: Release, right: Release) -> bool:
    left_ids = {track.identity() for track in left.tracks}
    right_ids = {track.identity() for track in right.tracks}
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


def is_remix_track(track: Track) -> bool:
    return bool(REMIX_TITLE_RE.search(track.title or ''))


def unique_tracks(release: Release, other: Release) -> list[Track]:
    other_ids = {track.identity() for track in other.tracks}
    return [
        track
        for track in release.tracks
        if track.identity() not in other_ids
    ]


def non_remix_unique_tracks(release: Release, other: Release) -> list[Track]:
    return [
        track
        for track in unique_tracks(release, other)
        if not is_remix_track(track)
    ]


def remix_unique_tracks(release: Release, other: Release) -> list[Track]:
    return [
        track
        for track in unique_tracks(release, other)
        if is_remix_track(track)
    ]


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

    chosen_core = {
        track.identity()
        for track in chosen.tracks
        if not is_remix_track(track)
    }
    other_core = {
        track.identity()
        for track in other.tracks
        if not is_remix_track(track)
    }

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

    return {
        'shared': compare['shared_identity_count'],
        'left_unique': compare['left_only_identity_count'],
        'right_unique': compare['right_only_identity_count'],
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
            if choose not in {'left', 'right', 'skip'}:
                raise ValueError(
                    f'Rule {index} has invalid "choose": {choose!r}. '
                    'Use left, right, or skip.'
                )
            # Parse and validate against a broad dummy context at load time.
            dummy = {
                'shared': 0,
                'left_unique': 0,
                'right_unique': 0,
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

            if evaluation and evaluation['choice'] in {'left', 'right', 'skip'}:
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
        self.skip_button = ttk.Button(
            actions,
            text='Skip comparison',
            command=lambda: self.answer('skip'),
            state='disabled',
        )
        self.skip_button.pack(anchor='center')
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
                    text=f'Rules: {rule_pack.name} ({stats["covered"]} covered / {stats["unresolved"]} unresolved)'
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
                    text=f'Rules: {rule_pack.name} ({stats["covered"]} covered / {stats["unresolved"]} unresolved)'
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
                self.rules_label.config(text=f'Rules: {rule_pack.name}')
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
