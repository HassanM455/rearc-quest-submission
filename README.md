# BLS Data Synchronization Script

## Table of contents

1. [Thought process behind design](#thought-process-behind-design)
2. [What the script does](#what-the-script-does)
3. [How to access the data in GCS](#how-to-access-the-data-in-gcs)
4. [How the LLM was prompted](#how-the-llm-was-prompted)

## Thought process behind design

The goal was to create a repeatable process for collecting Bureau of Labor Statistics productivity data files and publishing them to Google Cloud Storage.

I separated the storage design into two buckets:

- A private bucket that stores versioned files and `manifest.json`
- A public bucket that exposes only the latest version of each file using the original BLS filename

The private bucket preserves the BLS `Last-Modified` timestamp in each object name. This makes file versions easy to identify and prevents a new version from silently overwriting the previous one before synchronization is complete.

The manifest uses the original BLS filename as its key. Each entry stores the timestamped object name, source last-modified Unix timestamp, and SHA-256 checksum. Using the original filename as the key makes it possible to compare dataset versions even though the stored object names contain different timestamps.

The script uploads new files before deleting old versions and replaces the manifest only after the storage operations succeed. This reduces the chance that the manifest describes a state that was not fully written to GCS.

## What the script does

The script:

1. Requests the BLS directory page.
2. Parses the HTML to discover available files.
3. Sends a `HEAD` request for each file to read its `Last-Modified` value.
4. Downloads each file locally.
5. Calculates a SHA-256 checksum.
6. Builds a new manifest.
7. Compares it with the existing private-bucket manifest.
8. Adds, replaces, or deletes objects as needed.
9. Uploads the new manifest after synchronization succeeds.
10. Synchronizes the public bucket.
11. Generates and uploads `index.html`.
12. Deletes the local downloaded files.

The comparison covers these cases:

- **Initial load:** If no manifest exists, all discovered files are uploaded.
- **New source file:** If a BLS file is missing from the old manifest, it is added.
- **Updated source file:** If its timestamp or checksum changes, the new version replaces the old one.
- **Removed source file:** If a previously tracked file is no longer on the BLS server, its stored object is deleted.
- **Public synchronization:** The public bucket is kept aligned with the current source files, but timestamp suffixes are removed from object names.
- **Stale public files:** Public objects that are no longer part of the source dataset are deleted.
- **Manifest isolation:** `manifest.json` is kept only in the private bucket.
- **Public index:** `index.html` is regenerated so users can browse and download the current files.

| Bucket | Purpose |
|---|---|
| `rearc-quest-hassan-mahmood` | Stores timestamped source objects and `manifest.json` |
| `rearc-quest-public-hassan-mahmood` | Stores the latest files under their original names and exposes `index.html` |

## How to access the data in GCS

The public data can be browsed at:

`https://storage.googleapis.com/rearc-quest-public-hassan-mahmood/index.html`

Individual files can also be accessed directly. For example:

`https://storage.googleapis.com/rearc-quest-public-hassan-mahmood/pr.series`

The public bucket contains the latest version of each BLS file without the Unix timestamp suffix. The private bucket is not intended to be the user-facing access point.

## How the LLM was prompted

The script was developed incrementally instead of asking for the entire solution in one prompt.

The prompts were broken into these steps:

1. Retrieve filenames from the BLS HTML directory.
2. Read each file's last-modified timestamp.
3. Convert the timestamp to Unix time.
4. Download files with timestamped names.
5. Add a GCS manifest containing filenames and checksums.
6. Compare manifests and handle additions, updates, and deletions.
7. Synchronize a separate public bucket.
8. Remove timestamp suffixes from public object names.
9. Generate a public `index.html`.
10. Clean up downloaded local files.

This made it easier to test each part independently, correct compatibility issues, and review the design as the requirements expanded. The LLM was used as an implementation assistant, while the storage model, synchronization behavior, and validation steps were refined through iterative prompts.
