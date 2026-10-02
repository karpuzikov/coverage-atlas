# Coverage Atlas

**Status:** Under construction ⚠️  
**Current version:** 0.1.6

Coverage Atlas analyzes a music collection as a global coverage problem: preserve every meaningful track/version while finding the smallest sensible set of releases needed to provide that content.

## Download

[Download Coverage Atlas 0.1.6.pyw](https://raw.githubusercontent.com/karpuzikov/coverage-atlas/main/Coverage%20Atlas%200.1.6.pyw)

## Principles

- Preserve wanted musical content first.
- Minimize the number of releases.
- Prefer known CD editions over WEB editions when musical coverage is otherwise equivalent.
- Treat source/container folders separately from release folders.
- Among equally small release sets, prefer fewer actual audio files.
- Optimize the collection globally rather than comparing releases only in pairs.
- Recalculate when a release is excluded or forced to stay.
- Show which tracks make a release necessary.
- Highlight track versions that currently have no alternative provider.
- Do not silently merge ambiguous track identities.
- Never delete, move, or modify music during analysis.

## Current features

- Recursive music-library scan.
- Source/container detection for layouts such as `BT\\Deemix\\...` and `BT\\RED\\...`.
- Multi-disc release grouping across `CD1`, `CD 1`, `Disc 1`, `Disk 1`, and equivalent folders.
- Physical release folders remain one release even when individual track tags disagree; tag inconsistencies are logged instead of splitting the release.
- Explicit `CD` / `WEB` / `UNKNOWN` release classification using folder markers, Deemix/Deezer source context, and CUE/rip-log evidence.
- CD-preference optimization after coverage and release-count requirements are satisfied.
- Strong identity linking through MusicBrainz Recording IDs and ISRCs.
- Correct decoding of binary/freeform tag values used by formats such as ALAC/M4A.
- Safe metadata-to-identifier bridging when title, artist, and duration identify exactly one strong match.
- Punctuation/spacing-insensitive identity matching for equivalent metadata spellings.
- Conservative release-alignment matching for near-identical editions using track position, duration, existing overlap, and title/version evidence.
- Ambiguous identity matches remain separate and are logged.
- Global release coverage optimization.
- Exact optimization for tractable candidate sets.
- File-count tie-breaking for equally small release plans.
- Exclude release / Force keep / Clear decision.
- Automatic re-optimization after decisions.
- Release and track search.
- Per-release explanations.
- Irreplaceable-track highlighting.
- Interactive related-release view.
- Detailed JSONL diagnostic logging.

## Data isolation

All persistent app data is kept separately under:

`Documents\Karpuzikov Tools\Coverage Atlas\`

Diagnostic logs are stored in:

`Documents\Karpuzikov Tools\Coverage Atlas\logs\`

## Requirements

- Windows
- Python with Tkinter
- `mutagen` is installed automatically when missing

## Safety

Coverage Atlas analyzes the collection and produces recommendations. It does not delete, move, or modify music files.
