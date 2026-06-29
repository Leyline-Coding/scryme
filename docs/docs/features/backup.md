# Backup & restore

The **Backup** page (`/backup`, or the *backup* link on the home page) saves and restores your
data independently of the card database.

## What's in a backup

A single JSON file containing your **collection, decks, saved searches, wishlist, tags, and price
history**. The card database (`cards`) and ingest state are **not** included — they're rebuilt from
Scryfall — so backups stay small, portable, and survive re-ingests and version upgrades.

## Download

Click **Download backup (.json)** to save a file named `scryme-backup-<date>.json`. This works even
on the read-only demo. Keep it somewhere safe (a synced folder like Dropbox or Google Drive is a
good fit — and underpins the planned desktop app's backup story).

## Restore

Restoring **replaces** your current data with the file's contents:

1. Choose a backup file and click **Preview** to see exactly what it contains (counts per
   category), without changing anything.
2. Click **Restore (replace)** to apply it — this wipes your current collection/decks/etc. first,
   then loads the backup, all in one transaction.

Rows whose card isn't in the current database (for example, restoring onto a fresh instance before
an ingest) are **skipped** and reported, rather than failing the whole restore — run an
[ingest](../getting-started/self-hosting.md), then restore again. Restore is disabled on the
read-only demo; the download still works.
