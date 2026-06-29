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

## Automatic backups to a folder (and cross-device sync)

Point scryme at a folder with `SCRYME_BACKUP_DIR` and the Backup page gains a **Backups on disk**
section: a **Back up now** button, a list of the backups in that folder (each with download and a
preview → restore), and — when `SCRYME_BACKUP_INTERVAL_HOURS` is set — **scheduled** backups.
`SCRYME_BACKUP_KEEP` (default 14) bounds how many are kept; older ones are pruned automatically.

If that folder is one your OS syncs (Dropbox, Google Drive, iCloud Drive, Syncthing…), this doubles
as **cross-device sync**: each install writes backups into the shared folder, and another install
restores from the latest. There's no merge — restore **replaces** local data, so it's
last-writer-wins; treat one device as the source of truth at a time.

From the command line:

```bash
python -m src.cli backup --dir /path/to/folder   # write one now (honors SCRYME_BACKUP_DIR if unset)
python -m src.cli restore backup.json            # dry-run preview
python -m src.cli restore backup.json --apply    # replace your data
```

This is the engine behind the planned [desktop app](https://github.com/Leyline-Coding/scryme/issues/49)'s
backup story.

## Encrypted backups

Add a **passphrase** to encrypt a backup. On the download form, type a passphrase to get an
encrypted `.enc.json` file; from the CLI, `python -m src.cli backup --passphrase <pass>`. The file
is a small envelope — the JSON is encrypted with Fernet (AES-128 + HMAC) under a key derived from
your passphrase via PBKDF2-HMAC-SHA256, so nothing is readable without it.

Restoring an encrypted backup asks for the passphrase (a field on the restore form, or
`restore --passphrase <pass>`); a wrong passphrase or a tampered file fails cleanly without touching
your data. For **scheduled/on-disk** backups, set `SCRYME_BACKUP_PASSPHRASE` and every written
backup is encrypted automatically (and restore from disk uses it).

**There's no recovery if you lose the passphrase** — keep it somewhere safe.
