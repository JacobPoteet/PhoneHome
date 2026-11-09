# Tools

## backup.ps1: back up the archive to a drive you own (Windows)

Copies everything in the PhoneHome archive (the NAS share) to a folder you choose, such
as an external drive you keep somewhere else. Then it checks every recording against
`SHA256SUMS`, so you know the copy is good.

It is **additive only**:

- New files are copied, and nothing is ever deleted from the backup. A message deleted in
  the web app (and later purged from the trash) stays in your backup.
- Recordings never change. If one differs between the archive and the backup, the script
  doesn't overwrite it; it reports which copy is damaged.
- Edited details and transcripts are updated, and the previous copy is kept in
  `_previous\<date>\`.

### Run it

Open PowerShell in this folder and run:

```powershell
powershell -ExecutionPolicy Bypass -File .\backup.ps1 -Source \\nas\phonehome -Destination E:\PhoneHome-Backup
```

(`-ExecutionPolicy Bypass` applies to this one run only; Windows blocks downloaded
scripts by default.) Use the same destination every time: only new and changed files are
copied, so later runs take seconds plus the checksum check. Add `-SkipVerify` to skip the
checksum check on a slow USB drive.

Each run appends to `backup-log.txt` and writes `last-backup.json` in the destination. It
exits with code 1 if anything needs attention.

### Show it on the Backup page

Set `PHONEHOME_BACKUP_TOKEN` on the server (in `deploy/docker/.env`), then add:

```powershell
... -Server http://phonehome.lan:8080 -Token YOUR_BACKUP_TOKEN
```

The Backup page and the Homepage widget then show when you last backed up.

### Run it automatically

To run it every Sunday at 10:00 while the drive is plugged in:

```powershell
schtasks /Create /TN "PhoneHome backup" /SC WEEKLY /D SUN /ST 10:00 /TR "powershell -ExecutionPolicy Bypass -File C:\path\to\backup.ps1 -Source \\nas\phonehome -Destination E:\PhoneHome-Backup"
```

### Restore from a backup

The backup *is* an archive. Open `index.html` in it to browse and play everything with
no software at all. To rebuild PhoneHome from it, copy the folder back to the NAS share
(leave out `_previous`, `backup-log.txt` and `last-backup.json`), then run:

```bash
docker compose exec phonehome phonehome-server reindex
docker compose exec phonehome phonehome-server verify
```

`reindex` rebuilds the database from the files. You lose only "heard" marks; accounts
can be re-added with `adduser`.
