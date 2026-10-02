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

APP = 'Coverage Atlas'
VERSION = '0.1.6'
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


DISC_FOLDER_RE = re.compile(r'^(?:cd|disc|disk)\s*[-_ ]?\s*0*\d+(?:\s*(?:of|/)\s*\d+)?$', re.I)


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
    explicit_cd = bool(re.search(r'(?<![a-z0-9])cd(?![a-z0-9])', name_text))
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

    cd_evidence = []
    web_evidence = []
    if explicit_cd:
        cd_evidence.append('folder says CD')
    if has_cue:
        cd_evidence.append('CUE file')
    if has_log and has_cue:
        cd_evidence.append('rip log')
    if explicit_web:
        web_evidence.append('folder says WEB')
    if source_web:
        web_evidence.append(f'source container {source_container}')

    if cd_evidence and explicit_web:
        return 'UNKNOWN', '; '.join(cd_evidence + web_evidence + ['conflicting media evidence'])
    if cd_evidence:
        return 'CD', '; '.join(cd_evidence)
    if web_evidence:
        return 'WEB', '; '.join(web_evidence)
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
    tracks: list[Track] = field(default_factory=list)
    keys: set[str] = field(default_factory=set)


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
                album = tag(tags, 'album') or path.parent.name
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
                    )
                else:
                    existing = groups[rid]
                    conflicts = {}
                    for field_name, existing_value, new_value in (
                        ('album', existing.name, album),
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
                )
                groups[rid].tracks.append(track)
                all_tracks.append(track)
            except Exception as exc:
                failures += 1
                LOG.error('file_read_error', exc, relative_path=rel)

        self.releases = groups
        self.resolve_identities(all_tracks)

        for release in self.releases.values():
            release.keys = {track.key for track in release.tracks if track.key}

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
                track_count=len(release.tracks),
                distinct_identity_count=len(release.keys),
            )

        source_counts = Counter(track.identity_source for track in all_tracks)
        media_counts = Counter(release.media_type for release in self.releases.values())
        container_counts = Counter(release.source_container for release in self.releases.values())
        LOG.event(
            'scan_parsed',
            release_count=len(groups),
            file_failures=failures,
            identity_sources=dict(source_counts),
            release_media_types=dict(media_counts),
            source_containers=dict(container_counts),
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

        bridged = 0
        ambiguous = 0
        metadata_only_indexes: list[int] = []

        for idx, track in enumerate(tracks):
            root = dsu.find(idx)
            if root in strong_key_by_root:
                track.key = strong_key_by_root[root]
                track.identity_source = 'musicbrainz_recording_id' if track.key.startswith('mb:') else 'isrc'
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
        return {'CD': 0, 'UNKNOWN': 1, 'WEB': 2}.get(media_type, 1)

    def objective(self, selected: set[str]):
        media_penalty = sum(self.media_rank(self.releases[rid].media_type) for rid in selected)
        file_count = sum(len(self.releases[rid].tracks) for rid in selected)
        stable = tuple(sorted((self.releases[rid].date, self.releases[rid].name, rid) for rid in selected))
        return (len(selected), media_penalty, file_count, stable)

    def optimize(self):
        started = time.perf_counter()
        avail = set(self.releases) - self.excluded
        forced = self.forced & avail
        universe = set().union(*(self.releases[r].keys for r in avail)) if avail else set()
        covered = set().union(*(self.releases[r].keys for r in forced)) if forced else set()
        chosen = set(forced)
        remaining = universe - covered

        LOG.event(
            'optimize_start',
            release_count=len(self.releases),
            available_count=len(avail),
            forced=sorted(forced),
            excluded=sorted(self.excluded),
            identity_universe=len(universe),
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
                remaining_after=len(remaining),
                top_candidates=[
                    {
                        'release_id': cand,
                        'name': self.releases[cand].name,
                        'new_coverage': len(self.releases[cand].keys & (remaining | new_keys)),
                        'media_type': self.releases[cand].media_type,
                        'media_penalty': self.media_rank(self.releases[cand].media_type),
                        'file_count': len(self.releases[cand].tracks),
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
                    self.releases[rid].date,
                    self.releases[rid].name,
                ),
            )
            for rid in ordered:
                others = chosen - {rid}
                coverage = set().union(*(self.releases[x].keys for x in others)) if others else set()
                if universe <= coverage:
                    chosen.remove(rid)
                    changed = True
                    LOG.event(
                        'redundancy_elimination',
                        removed_release_id=rid,
                        removed_release=self.releases[rid].name,
                        removed_file_count=len(self.releases[rid].tracks),
                        remaining_selected=len(chosen),
                    )

        optional = sorted(avail - forced)
        best = set(chosen)
        best_obj = self.objective(best)
        stats = {
            'nodes': 0,
            'pruned_size': 0,
            'pruned_lower_bound': 0,
            'solutions': 0,
            'improvements': 0,
            'tie_improvements': 0,
        }

        if len(optional) <= EXACT_SEARCH_RELEASE_LIMIT:
            basecov = set().union(*(self.releases[r].keys for r in forced)) if forced else set()
            LOG.event(
                'exact_search_start',
                optional_count=len(optional),
                seed_release_count=best_obj[0],
                seed_media_penalty=best_obj[1],
                seed_file_count=best_obj[2],
                base_coverage=len(basecov),
            )

            def dfs(selected: set[str], coverage: set[str]):
                nonlocal best, best_obj
                stats['nodes'] += 1
                missing = universe - coverage
                if not missing:
                    stats['solutions'] += 1
                    obj = self.objective(selected)
                    if obj < best_obj:
                        if obj[0] == best_obj[0]:
                            stats['tie_improvements'] += 1
                        stats['improvements'] += 1
                        best = set(selected)
                        best_obj = obj
                        LOG.event(
                            'exact_best_improved',
                            selected_count=obj[0],
                            selected_media_penalty=obj[1],
                            selected_file_count=obj[2],
                            selected_release_ids=sorted(best),
                        )
                    return

                if len(selected) >= best_obj[0]:
                    stats['pruned_size'] += 1
                    return

                max_new = max(
                    (len(self.releases[rid].keys & missing) for rid in avail - selected),
                    default=0,
                )
                if max_new <= 0:
                    return
                lower_bound = math.ceil(len(missing) / max_new)
                if len(selected) + lower_bound > best_obj[0]:
                    stats['pruned_lower_bound'] += 1
                    return

                key = min(missing, key=lambda item: len((self.cover[item] & avail) - selected))
                providers = sorted(
                    (self.cover[key] & avail) - selected,
                    key=lambda rid: (
                        -len(self.releases[rid].keys & missing),
                        self.media_rank(self.releases[rid].media_type),
                        len(self.releases[rid].tracks),
                        self.releases[rid].date,
                        self.releases[rid].name,
                        rid,
                    ),
                )
                for rid in providers:
                    dfs(selected | {rid}, coverage | self.releases[rid].keys)

            dfs(set(forced), basecov)
            LOG.event(
                'exact_search_complete',
                best_release_count=best_obj[0],
                best_media_penalty=best_obj[1],
                best_file_count=best_obj[2],
                **stats,
            )
        else:
            LOG.event(
                'exact_search_skipped',
                optional_count=len(optional),
                limit=EXACT_SEARCH_RELEASE_LIMIT,
                reason='candidate_count_above_limit',
            )

        self.kept = best
        self.reason = {}
        kept_counts = Counter(key for rid in self.kept for key in self.releases[rid].keys)

        for rid, release in self.releases.items():
            if rid in self.excluded:
                self.reason[rid] = 'Excluded by user'
            elif rid in self.kept:
                unique = [key for key in release.keys if kept_counts[key] == 1]
                if unique:
                    self.reason[rid] = f'Kept: supplies {len(unique)} required track version(s) not supplied by another kept release'
                else:
                    self.reason[rid] = 'Kept: part of the smallest coverage plan found'
            elif not release.keys:
                self.reason[rid] = 'Irrelevant: contributes no analyzable tracks'
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

        columns = ('status', 'date', 'release', 'media', 'source', 'tracks', 'unique', 'barcode')
        self.tree = ttk.Treeview(left, columns=columns, show='headings')
        for column, width in (
            ('status', 100), ('date', 95), ('release', 330), ('media', 75), ('source', 95),
            ('tracks', 65), ('unique', 70), ('barcode', 135)
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
                    release.source_container, len(release.tracks), unique, release.barcode
                ),
            )

        kept_files = sum(len(self.m.releases[rid].tracks) for rid in self.m.kept)
        kept_media = Counter(self.m.releases[rid].media_type for rid in self.m.kept)
        self.summary.config(
            text=(
                f'{len(self.m.kept)} kept / {len(self.m.releases)} releases - {kept_files} files - '
                f'CD:{kept_media.get("CD", 0)} WEB:{kept_media.get("WEB", 0)} '
                f'UNKNOWN:{kept_media.get("UNKNOWN", 0)}'
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
        if before and not messagebox.askyesno(
            'Recalculate',
            f'This release currently uniquely supplies {len(before)} track version(s) within the kept plan.\n\n'
            'Exclude it and recalculate alternatives?',
        ):
            return
        LOG.event(
            'user_decision',
            action='exclude',
            release_id=rid,
            release=self.m.releases[rid].name,
            unique_in_current_plan=len(before),
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
            f'Folder: {release.folder}\n\n'
            f'{self.status_of(rid)}\n'
            f'{self.m.reason.get(rid, "")}\n\n'
            'TRACKS\n',
        )
        for i, track in enumerate(release.tracks, 1):
            flag = '  [IRREPLACEABLE IN CURRENT PLAN]' if track.key in impact else ''
            alternatives = len(self.m.cover[track.key] - {rid})
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
