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
from array import array
from concurrent.futures import ThreadPoolExecutor, as_completed

APP = 'Coverage Atlas Reasoning Lab'
VERSION = '0.1.20'
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
SETTINGS_FILE = DATA / 'settings.json'
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


def load_app_settings() -> dict:
    try:
        data = json.loads(SETTINGS_FILE.read_text('utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_app_settings(settings: dict):
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp = SETTINGS_FILE.with_suffix('.tmp')
        temp.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
        temp.replace(SETTINGS_FILE)
    except Exception as exc:
        LOG.error('settings_save_error', exc, settings_file=str(SETTINGS_FILE))


def remembered_initial_dir(path_value: str, expect_file: bool = False) -> str | None:
    if not path_value:
        return None
    try:
        path = Path(path_value)
        candidate = path.parent if expect_file else path
        if candidate.is_dir():
            return str(candidate)
    except Exception:
        pass
    return None


LOG.event('settings_loaded', settings_file=str(SETTINGS_FILE))


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
            **hidden_subprocess_kwargs(),
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
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **hidden_subprocess_kwargs(),
        )
        importlib.invalidate_caches()
        LOG.event('dependency_install_complete', dependency='mutagen', target=str(DEPS))
    except Exception as exc:
        LOG.error('dependency_install_failed', exc, dependency='mutagen', target=str(DEPS))
        raise


ensure_winget()
ensure_mutagen()

from mutagen import File as MFile


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
        key = str(path.resolve()).casefold()
        with _LOGCHECKER_LOCK:
            cache = read_logchecker_cache()
            old = cache.get(key, {})
            same_file = (
                old.get('size') == stat.st_size and
                old.get('mtime_ns') == stat.st_mtime_ns and
                old.get('checker') == 'ligh7s/hey-bro-check-log' and
                old.get('checker_commit') == HEYBROCHECKLOG_COMMIT
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

                if unrecognized and clearly_supported_xld_log(path):
                    LOG.event(
                        'embedded_heybrochecklog_xld_retry',
                        path=str(path),
                        first_error=str(unrecognized),
                    )
                    repaired = ensure_heybrochecklog(force=True)
                    if repaired:
                        parsed = repaired(path, False)
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


def is_organizational_container(path: Path) -> bool:
    return bool(ORGANIZATIONAL_FOLDER_RE.fullmatch(path.name.strip()))


def base_album_name(value: str) -> str:
    value = (value or '').strip()
    patterns = (
        r'\s*[:\-]?\s*(?:cd|disc|disk)\s*0*\d+(?:\s*(?:of|/)\s*\d+)?\s*$',
        r'\s*\((?:cd|disc|disk)\s*0*\d+(?:\s*(?:of|/)\s*\d+)?\)\s*$',
        r'\s*\[(?:cd|disc|disk)\s*0*\d+(?:\s*(?:of|/)\s*\d+)?\]\s*$',
    )
    previous = None
    while value and value != previous:
        previous = value
        for pattern in patterns:
            value = re.sub(pattern, '', value, flags=re.I).strip()
    return value


def inferred_disc_number(raw_album: str, path: Path, release_folder: Path) -> str:
    album_match = re.search(
        r'(?i)(?:^|[\s:\-\(\[])(?:cd|disc|disk)\s*0*(\d+)'
        r'(?:\s*(?:of|/)\s*\d+)?[\s\)\]]*$',
        raw_album or '',
    )
    if album_match:
        return album_match.group(1)

    current = path.parent
    while current != release_folder and current != current.parent:
        if is_disc_folder(current):
            match = re.search(r'\d+', current.name)
            if match:
                return match.group(0)
        current = current.parent
    return ''


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
    sample_rate: int = 0
    channels: int = 0
    dynamic_range: int | None = None
    dynamic_range_db: float | None = None
    dynamic_range_error: str = ''

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
    dynamic_range: int | None = None
    dynamic_range_db: float | None = None
    dynamic_range_measured_tracks: int = 0
    dynamic_range_complete: bool = False

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
            'dynamic_range': self.dynamic_range,
            'dynamic_range_db': self.dynamic_range_db,
            'dynamic_range_measured_tracks': self.dynamic_range_measured_tracks,
            'dynamic_range_complete': self.dynamic_range_complete,
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
                    'sample_rate': track.sample_rate,
                    'channels': track.channels,
                    'dynamic_range': track.dynamic_range,
                    'dynamic_range_db': track.dynamic_range_db,
                    'dynamic_range_error': track.dynamic_range_error,
                    'relative_path': track.relative_path,
                    'identity': track.identity(),
                }
                for track in self.tracks
            ],
        }



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


def _centi_db_key(amplitude: float) -> int:
    return _round_half_away(2000.0 * math.log10(amplitude))


class _DRChannel:
    __slots__ = (
        'sum_squares', 'peak', 'histogram', 'valid_windows',
        'primary', 'secondary', 'saw_nonzero'
    )

    def __init__(self):
        self.sum_squares = 0.0
        self.peak = 0.0
        self.histogram = [0] * 10001
        self.valid_windows = 0
        self.primary = None
        self.secondary = None
        self.saw_nonzero = False

    def add(self, sample: float):
        magnitude = abs(float(sample))
        self.sum_squares += magnitude * magnitude
        if magnitude > self.peak:
            self.peak = magnitude
        if magnitude != 0.0:
            self.saw_nonzero = True

    def _observe_peak(self, amplitude: float):
        if amplitude <= 0.0:
            return
        candidate = (amplitude, _centi_db_key(amplitude))
        if self.primary is None:
            self.primary = candidate
        elif candidate[1] > self.primary[1]:
            self.secondary = self.primary
            self.primary = candidate
        elif self.secondary is None or candidate[1] > self.secondary[1]:
            self.secondary = candidate

    def finish_window(self, frames: int):
        if frames <= 0:
            return
        rms2 = 2.0 * self.sum_squares / float(frames)
        rms = math.sqrt(max(0.0, rms2))
        if rms > 0.0:
            key = max(-10000, min(0, _centi_db_key(rms)))
            self.histogram[key + 10000] += 1
        self.valid_windows += 1
        self._observe_peak(self.peak)
        self.sum_squares = 0.0
        self.peak = 0.0

    def result(self) -> float | None:
        if self.valid_windows <= 0:
            return None
        if not self.saw_nonzero:
            return 0.0

        primary = self.primary[0] if self.primary else 0.0
        secondary = self.secondary[0] if self.secondary else None
        selected_peak = secondary if secondary and secondary > 0.0 else primary

        target = max(self.valid_windows // 5, 1)
        selected_count = 0
        selected_power = 0.0
        for bin_index in range(10000, -1, -1):
            count = self.histogram[bin_index]
            if not count:
                continue
            bin_db = -100.0 + bin_index * 0.01
            selected_count += count
            selected_power += (10.0 ** (bin_db / 10.0)) * count
            if selected_count >= target:
                break

        if selected_count <= 0 or selected_peak <= 0.0:
            return 0.0

        loud_rms = math.sqrt(selected_power / selected_count)
        if loud_rms <= 0.0:
            return 0.0

        dr = -20.0 * math.log10(loud_rms / selected_peak)
        if dr < 0.0 and primary > 0.0:
            dr = max(0.0, -20.0 * math.log10(loud_rms / primary))
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
    states = [_DRChannel() for _ in range(channels)]
    frames_in_window = 0
    channel_index = 0
    byte_remainder = b''

    try:
        proc = subprocess.Popen(
            [
                ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-xerror',
                '-i', str(path), '-map', '0:a:0',
                '-f', 'f32le', '-acodec', 'pcm_f32le', '-',
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **hidden_subprocess_kwargs(),
        )

        assert proc.stdout is not None
        while True:
            chunk = proc.stdout.read(1024 * 1024)
            if not chunk:
                break
            data = byte_remainder + chunk
            usable = len(data) - (len(data) % 4)
            if usable <= 0:
                byte_remainder = data
                continue
            values = array('f')
            values.frombytes(data[:usable])
            if sys.byteorder != 'little':
                values.byteswap()
            byte_remainder = data[usable:]

            for value in values:
                states[channel_index].add(value)
                channel_index += 1
                if channel_index == channels:
                    channel_index = 0
                    frames_in_window += 1
                    if frames_in_window == window_frames:
                        for state in states:
                            state.finish_window(frames_in_window)
                        frames_in_window = 0

        stderr = proc.stderr.read().decode('utf-8', 'replace').strip() if proc.stderr else ''
        return_code = proc.wait()
        if return_code != 0:
            return {
                'dr': None,
                'dr_db': None,
                'error': stderr or f'ffmpeg exited with code {return_code}',
                'algorithm': DR_ALGORITHM_REVISION,
            }

        if frames_in_window > 0:
            for state in states:
                state.finish_window(frames_in_window)

        channel_drs = [value for value in (state.result() for state in states) if value is not None]
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


def measure_dynamic_range_tracks(tracks: list[Track], progress=lambda done, total: None):
    if not tracks:
        return

    def worker(track: Track):
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
                path=track.relative_path,
                dr=track.dynamic_range,
                dr_db=track.dynamic_range_db,
                error=track.dynamic_range_error,
            )


def finalize_release_dynamic_range(release: Release):
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


def dynamic_range_display(release: Release) -> str:
    if release.dynamic_range is not None:
        return (
            f'DR{release.dynamic_range} '
            f'({release.dynamic_range_measured_tracks}/{len(release.tracks)} tracks)'
        )
    if release.dynamic_range_measured_tracks:
        return (
            f'partial '
            f'({release.dynamic_range_measured_tracks}/{len(release.tracks)} tracks)'
        )
    if not find_ffmpeg():
        return 'unavailable (ffmpeg not found)'
    return 'unavailable'

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
            sample_rate = int(getattr(info, 'sample_rate', 0) or 0)
            channels = int(getattr(info, 'channels', 0) or 0)

            title = tag(tags, 'title') or path.stem
            artist = tag(tags, 'artist')
            raw_album = tag(tags, 'album')
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
            album = base_album_name(raw_album) or release_folder.name
            if not discno:
                discno = inferred_disc_number(raw_album, path, release_folder)

            try:
                relative_folder = str(release_folder.relative_to(root))
            except Exception:
                relative_folder = release_folder.name

            # A physical release folder is one release. CD1/CD2/Disc 1/Disc 2
            # are media inside that release and must never become independent
            # choices. Only true organizational containers still use tags to
            # distinguish multiple releases sharing one directory.
            if is_organizational_container(release_folder):
                release_seed = '|'.join([
                    'tagged-release',
                    relative_folder.casefold(),
                    clean_identifier(mbrel),
                    compact(album),
                    clean_identifier(barcode),
                ])
            else:
                try:
                    physical_key = str(release_folder.resolve())
                except Exception:
                    physical_key = str(release_folder)
                release_seed = 'physical-release|' + physical_key.casefold()

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
            else:
                existing = groups[rid]
                conflicts = {}
                if existing.name and album and compact(existing.name) != compact(album):
                    conflicts['album'] = {
                        'release': existing.name,
                        'track': album,
                        'raw_track_album': raw_album,
                    }
                for field_name, existing_value, new_value in (
                    ('barcode', existing.barcode, barcode),
                    ('musicbrainz_release_id', existing.mbrel, mbrel),
                ):
                    if (
                        existing_value and new_value and
                        clean_identifier(existing_value) != clean_identifier(new_value)
                    ):
                        conflicts[field_name] = {
                            'release': existing_value,
                            'track': new_value,
                        }
                if conflicts:
                    LOG.event(
                        'release_tag_inconsistency',
                        release_id=rid,
                        relative_path=str(path.relative_to(root)),
                        physical_release_folder=str(release_folder),
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
                    sample_rate=sample_rate,
                    channels=channels,
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

    all_tracks = [
        track
        for release in groups.values()
        for track in release.tracks
    ]
    measure_dynamic_range_tracks(all_tracks, progress)
    for release in groups.values():
        finalize_release_dynamic_range(release)

    logchecker_scored = 0
    logchecker_candidate_count = 0
    for release in groups.values():
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

    LOG.event(
        'scan_complete',
        root=str(root),
        release_count=len(groups),
        audio_file_count=len(files),
        file_failures=failures,
        logchecker_scored_release_count=logchecker_scored,
        logchecker_candidate_release_count=logchecker_candidate_count,
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
        'left_dynamic_range': left.dynamic_range,
        'right_dynamic_range': right.dynamic_range,
        'left_dynamic_range_db': left.dynamic_range_db,
        'right_dynamic_range_db': right.dynamic_range_db,
        'dynamic_range_comparable': left.dynamic_range is not None and right.dynamic_range is not None,
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
        'left_dynamic_range': left.dynamic_range,
        'right_dynamic_range': right.dynamic_range,
        'left_dynamic_range_db': left.dynamic_range_db,
        'right_dynamic_range_db': right.dynamic_range_db,
        'dynamic_range_comparable': left.dynamic_range is not None and right.dynamic_range is not None,
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
                'left_dynamic_range': None,
                'right_dynamic_range': None,
                'left_dynamic_range_db': None,
                'right_dynamic_range_db': None,
                'dynamic_range_comparable': False,
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
        self.settings = load_app_settings()

        self.title(f'{APP} {VERSION} - {STATUS}')
        self.geometry('1500x900')
        self.minsize(1150, 700)
        self.configure(bg='#14171b')
        self._style()
        self._build()
        self.protocol('WM_DELETE_WINDOW', self.on_close)
        self.after(150, self.restore_previous_inputs)

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

    def restore_previous_inputs(self):
        """Automatically restore the last rules file and test folder on startup."""
        restored_rules = False
        remembered_rules = str(self.settings.get('last_selection_rules_path') or '').strip()

        candidate_rules = None
        if remembered_rules:
            path = Path(remembered_rules)
            if path.is_file():
                candidate_rules = path

        if candidate_rules is None and RULES_FILE.is_file():
            candidate_rules = RULES_FILE

        if candidate_rules is not None:
            try:
                rule_pack = SelectionRulePack.load(candidate_rules)
                try:
                    same_file = candidate_rules.resolve() == RULES_FILE.resolve()
                except Exception:
                    same_file = str(candidate_rules) == str(RULES_FILE)

                if not same_file:
                    shutil.copy2(candidate_rules, RULES_FILE)
                    rule_pack = SelectionRulePack.load(RULES_FILE)

                self.rules_label.config(
                    text=(
                        f'Rules: {rule_pack.name} v{rule_pack.version} '
                        f'({len(rule_pack.rules)} rules - auto-loaded)'
                    )
                )
                restored_rules = True
                LOG.event(
                    'selection_rules_startup_restored',
                    remembered_path=remembered_rules,
                    loaded_path=str(candidate_rules),
                    cached_path=str(RULES_FILE),
                    rule_pack_name=rule_pack.name,
                    rule_pack_version=rule_pack.version,
                    rule_count=len(rule_pack.rules),
                )
            except Exception as exc:
                LOG.error(
                    'selection_rules_startup_restore_error',
                    exc,
                    remembered_path=remembered_rules,
                    candidate_path=str(candidate_rules),
                )
                self.rules_label.config(text='Selection rules failed to auto-load')

        remembered_folder = str(self.settings.get('last_test_folder') or '').strip()
        if remembered_folder:
            folder = Path(remembered_folder)
            if folder.is_dir():
                LOG.event(
                    'test_folder_startup_restore',
                    path=str(folder),
                    rules_restored=restored_rules,
                )
                self.start_folder_scan(folder, remember=False)
                return

            LOG.event(
                'test_folder_startup_restore_missing',
                path=remembered_folder,
            )
            self.folder_label.config(text=f'Last test folder missing: {remembered_folder}')

        if restored_rules:
            self.status.config(text='Previous selection rules loaded. No valid previous test folder found.')

    def choose_folder(self):
        last_folder = str(self.settings.get('last_test_folder') or '')
        dialog_args = {'title': 'Select test music folder'}
        initialdir = remembered_initial_dir(last_folder)
        if initialdir:
            dialog_args['initialdir'] = initialdir

        folder = filedialog.askdirectory(**dialog_args)
        if not folder:
            return

        self.start_folder_scan(Path(folder), remember=True)

    def start_folder_scan(self, folder: Path, remember: bool = True):
        folder = Path(folder)
        if not folder.is_dir():
            messagebox.showerror(
                APP,
                f'Test folder does not exist:\n\n{folder}',
            )
            return

        if self.scan_thread and self.scan_thread.is_alive():
            return

        if remember:
            self.settings['last_test_folder'] = str(folder)
            save_app_settings(self.settings)
            LOG.event('last_test_folder_saved', path=self.settings['last_test_folder'])

        self.root_folder = folder
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
                            text=f'Scanning / measuring audio {d:,}/{t:,}...'
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
        last_rules = str(self.settings.get('last_selection_rules_path') or '')
        dialog_args = {
            'title': 'Select Coverage Atlas selection rules',
            'filetypes': [
                ('JSON selection rules', '*.json'),
                ('All files', '*.*'),
            ],
        }
        initialdir = remembered_initial_dir(last_rules, expect_file=True)
        if initialdir:
            dialog_args['initialdir'] = initialdir
        if last_rules:
            try:
                last_name = Path(last_rules).name
                if last_name:
                    dialog_args['initialfile'] = last_name
            except Exception:
                pass

        source = filedialog.askopenfilename(**dialog_args)
        if not source:
            return

        try:
            rule_pack = SelectionRulePack.load(Path(source))
            shutil.copy2(source, RULES_FILE)
            rule_pack = SelectionRulePack.load(RULES_FILE)

            self.settings['last_selection_rules_path'] = str(Path(source))
            save_app_settings(self.settings)
            LOG.event(
                'last_selection_rules_path_saved',
                path=self.settings['last_selection_rules_path'],
            )

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
            f"Dynamic range: {dynamic_range_display(release)}\n"
            f"Logchecker: {logchecker_display(release)}"
        )

    def track_text(self, release: Release, other: Release, side: str) -> str:
        other_ids = {semantic_identity(track) for track in other.tracks}
        lines = []
        disc_numbers = {
            number_from_tag(track.discno, 1)
            for track in release.tracks
        }
        multi_disc = len(disc_numbers) > 1
        for index, track in enumerate(release.tracks, 1):
            shared = semantic_identity(track) in other_ids
            mark = '=' if shared else side
            duration = f'{track.duration / 60:.2f}' if track.duration else '-'
            identity = (
                f'ISRC:{track.isrc}' if track.isrc else
                f'MB:{track.mbrec}' if track.mbrec else
                'metadata'
            )
            dr_text = (
                f'DR{track.dynamic_range}'
                if track.dynamic_range is not None
                else 'DR?'
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

            if multi_disc:
                disc = number_from_tag(track.discno, 1)
                track_number = number_from_tag(track.trackno, index)
                position = f'D{disc}-{track_number:02d}'
            else:
                position = f'{index:02d}'

            lines.append(
                f'[{mark}] {position}. {track.artist} - {track.title} '
                f'[{duration}] [{track.extension or "-"}] [{dr_text}] [{identity}]{suffix}'
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
