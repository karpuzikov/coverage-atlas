# Coverage Atlas

**Status:** Under construction ⚠️  
**Current version:** 0.1.10

Coverage Atlas analyzes a music collection as a global coverage problem: preserve every meaningful track/version while finding the smallest sensible set of releases needed to provide that content.

## Download

[Download Coverage Atlas 0.1.10.pyw](https://raw.githubusercontent.com/karpuzikov/coverage-atlas/main/Coverage%20Atlas%200.1.10.pyw)

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
- Ignored tracks are explicitly marked in the release detail view and diagnostic logs.
- Releases containing only ignored remix/live material become irrelevant automatically.
- Remix tracks and live recordings are visible but excluded from coverage and optimization.
- Explicit WEB/CD folder markers take precedence over incidental CUE/log files, and names such as `2CD` are recognized as CD evidence.
- Multi-disc album names are normalized so `CD1` / `Disc 1` suffixes do not become the release title.
- Organizational folders such as numbered `Remixes` or `Tracks` containers are not treated as one giant release; direct loose audio files are modeled individually.
- Excluding the last provider of a track/version no longer makes that content disappear from the universe; lost content is explicitly reported.
- MusicBrainz Recording IDs and ISRCs are conservatively reconciled when artist, title/version, and duration evidence agree.
- Large collections are solved exactly by mandatory-release propagation and independent coverage components instead of a global release-count cutoff.

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


## Reasoning Lab addon

**Status:** Under construction ⚠️  
**Version:** 0.1.4

[Download Coverage Atlas Reasoning Lab 0.1.4.pyw](https://raw.githubusercontent.com/karpuzikov/coverage-atlas/main/Coverage%20Atlas%20Reasoning%20Lab%200.1.4.pyw)

The Reasoning Lab is an isolated pairwise-preference trainer for learning human release-selection rules before adding them to Coverage Atlas.

- Import a test music folder.
- Generate only meaningful comparison pairs: releases must share track identities or strong same-release evidence.
- Show each meaningful pair at most once for the lifetime of that folder/session.
- Choose Release A, Release B, or Skip.
- Optionally record the reason for each pairwise choice.
- Add a persistent independent comment to either release, such as `Keep - contains unique song X`; the comment follows that release across later pairs and is saved even when the pair is skipped.
- Reuse previous reasons/comments from a clickable saved-answer library; clicking inserts the saved text into the active field so it can be extended.
- Stop at any time and export a timestamped training snapshot instead of completing the whole pair pool.
- Save every answer immediately and resume the same session after closing or updating the app.
- Log each presented pair, choice, reason, metadata snapshot, and comparison context.
- Maintain a clean JSONL reasoning dataset suitable for iterative rule extraction.
- Training workflow is iterative: collect answers -> export snapshot -> derive selection rules -> rerun the same folder -> generate only unresolved cases for the next round.
- Never modify, move, or delete music.
- Keep all persistent data isolated under `Documents\Karpuzikov Tools\Coverage Atlas Reasoning Lab\`.


### Iterative rule-gap workflow

The Reasoning Lab is designed for repeated training rounds rather than exhaustive pair completion:

1. Answer as many meaningful pairs as useful.
2. Use **Finish round / export snapshot**.
3. Derive a selection rule pack from that JSONL snapshot.
4. Load the generated `selection_rules.json` with **Load selection rules**.
5. Re-import the same test folder.
6. Pairs already answered are never shown again.
7. Pairs confidently covered by the rules are automatically removed from the training queue.
8. Only unresolved cases become the next comparison round.

The rule pack is an ordered JSON rule list. Each rule contains an `id`, a safe boolean `when` expression, and `choose` set to `left`, `right`, or `skip`. The Lab logs every rule-covered and unresolved pair so later rule revisions can be based on evidence rather than guesses.

Previously entered pair reasons and per-release comments are collected into a clickable saved-answer library. Clicking an entry inserts it into whichever comment/reason field was active most recently, where it can then be edited or extended.


### Automatic motivation inference

Reasoning Lab 0.1.3 is optimized for fast binary choices:

- The normal workflow is now simply **Choose Release A** or **Choose Release B**.
- Each choose button is centered directly below its respective release panel.
- The Lab compares the chosen and rejected release and infers the likely motivation from measurable differences and the user's historical answers.
- High-confidence cases are saved automatically with the inferred motivation.
- If the choice contains a real trade-off or no clear explanation can be inferred, the Lab opens a targeted **What motivated this choice?** dialog.
- The ambiguity dialog shows the conflicting evidence, candidate explanations, and previously saved motivations/comments for one-click reuse.
- Inferred motivations, confidence, categories, candidate evidence, and user clarifications are all written to the JSONL training dataset.
- Existing answers from earlier versions remain part of the historical preference profile.


### Files to send to ChatGPT

You do not need to search through the app's data folders. Reasoning Lab maintains one flat handoff folder:

`Documents\Karpuzikov Tools\Coverage Atlas Reasoning Lab\Files to Send ChatGPT\`

It contains only the current files useful for analysis:

- `Training Snapshot.jsonl`
- `Reasoning Dataset.jsonl`
- `Session State.json`
- `Diagnostic Log.jsonl`

Use **Open files to send** to open that exact folder directly. Exporting a training snapshot refreshes the folder automatically.
