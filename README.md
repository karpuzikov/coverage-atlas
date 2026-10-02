# Coverage Atlas

**Status:** Under construction ⚠️  
**Current version:** 0.1.1

Coverage Atlas analyzes a music collection as a global coverage problem: preserve every meaningful track/version while finding the smallest sensible set of releases needed to provide that content.

## Download

[Download Coverage Atlas.pyw](https://raw.githubusercontent.com/karpuzikov/coverage-atlas/main/Coverage_Atlas.pyw)

## Principles

- Preserve wanted musical content first.
- Minimize redundant releases.
- Optimize the collection globally rather than comparing releases only in pairs.
- Recalculate when a release is excluded or forced to stay.
- Show which tracks make a release necessary.
- Highlight track versions that currently have no alternative provider.
- Keep uncertain or incomplete cases visible instead of silently forcing a conclusion.
- Never delete music as part of analysis.

## Current features

- Recursive music-library scan.
- Track/version identity using available MusicBrainz Recording IDs, ISRCs, or metadata fallback.
- Global release coverage optimization.
- Exact optimization for tractable candidate sets, with deterministic fallback behavior.
- Exclude release / Force keep / Clear decision.
- Automatic re-optimization after decisions.
- Release and track search.
- Per-release explanations.
- Irreplaceable-track highlighting.
- Interactive related-release view.
- Detailed JSONL diagnostic logging for improving matching and optimization behavior.

## Data isolation

All persistent app data is kept separately under:

`Documents\Karpuzikov Tools\Coverage Atlas\`

including logs, cache, temp data, and settings.

Diagnostic logs are stored in:

`Documents\Karpuzikov Tools\Coverage Atlas\logs\`

## Requirements

- Windows
- Python with Tkinter
- `mutagen` is installed automatically when missing

## Safety

Coverage Atlas analyzes the collection and produces recommendations. It does not delete, move, or modify music files.
