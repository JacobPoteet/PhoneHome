# Running the server on Proxmox

The server runs in Docker inside an unprivileged Debian LXC. The archive lives on the NAS,
mounted on the Proxmox host and passed into the LXC. The database stays on the LXC's own
disk. See docs/PLAN.md §7 for why it's set up this way.

```
NAS share ──(SMB/NFS mount on the Proxmox host)──► /mnt/nas/phonehome
                                                      │ bind mount (mp0)
LXC "phonehome" ◄─────────────────────────────────────┘ /mnt/phonehome-archive
  └─ docker compose: phonehome container
       /archive ◄── /mnt/phonehome-archive   (messages, greetings, SHA256SUMS)
       /data    ◄── ./data                   (SQLite index, sessions, behavior.json)
```

## 1. Create the LXC

In the Proxmox UI: **Create CT**, Debian 12 template, unprivileged, 4 cores, 4 GB RAM,
16 GB disk. Under **Options → Features**, enable **nesting** (and **keyctl**). Those are
needed for Docker. Then inside the container:

```bash
apt update && apt install -y curl git
curl -fsSL https://get.docker.com | sh
```

## 2. Mount the NAS share on the Proxmox host

Unprivileged containers can't mount SMB/NFS themselves, so the **host** mounts it. On the
Proxmox host (SMB example; NFS works the same way):

```bash
mkdir -p /mnt/nas/phonehome
# /etc/fstab
//nas.lan/phonehome  /mnt/nas/phonehome  cifs  credentials=/root/.nas-cred,uid=101000,gid=101000,file_mode=0664,dir_mode=0775,_netdev,nofail  0  0
mount /mnt/nas/phonehome
```

**The UID gotcha:** in an unprivileged LXC, user 1000 inside the container is user
**101000** on the host (the host adds 100000). The `uid=101000,gid=101000` options above
make the files on the share owned by the container's user 1000. That matches
`PUID=1000`/`PGID=1000` in `.env`. For NFS, set the ownership on the NAS side to the same
numbers instead.

Then pass it into the container (replace `105` with your CT id):

```bash
pct set 105 -mp0 /mnt/nas/phonehome,mp=/mnt/phonehome-archive
pct reboot 105
```

Check from inside the LXC that user 1000 can write there:

```bash
setpriv --reuid=1000 --regid=1000 --clear-groups touch /mnt/phonehome-archive/test && rm /mnt/phonehome-archive/test
```

## 3. Start PhoneHome

Inside the LXC:

```bash
git clone https://github.com/JacobPoteet/PhoneHome.git /opt/phonehome
cd /opt/phonehome/deploy/docker
cp .env.example .env
nano .env                          # fill in the tokens (generate as shown in the file)
mkdir -p data && chown 1000:1000 data
docker compose up -d --build
docker compose logs -f             # Ctrl+C to stop following
```

The first build takes a few minutes (it builds the web app and installs the speech
model's runtime). Open `http://<lxc-ip>:8080` and create the first account in the browser.

The first transcription downloads the Whisper model (about 1.6 GB for `large-v3-turbo`)
into `./data/models`, so the LXC needs internet access then. Give the LXC 4 GB of RAM if
you keep the large model; `medium.en` or `small.en` need less and run faster. To add the
second account:

```bash
docker compose exec phonehome phonehome-server adduser NAME
```

Give the LXC a fixed IP or DNS name (e.g. `phonehome.lan`). The phone's Ansible
`vars.yml` uses it as `phonehome_server_url`, and `phonehome_server_token` must equal
`PHONEHOME_AGENT_TOKEN` here.

## Updating

```bash
cd /opt/phonehome && git pull
cd deploy/docker && docker compose up -d --build
```

## Useful commands

```bash
docker compose exec phonehome phonehome-server verify     # check every file's checksum
docker compose exec phonehome phonehome-server reindex    # rebuild the database from the archive
docker compose exec phonehome phonehome-server adduser NAME
```

## HTTPS and remote access

LAN-only plain HTTP works for everything except **recording greetings in the browser**:
browsers only allow the microphone on HTTPS (or localhost). Uploading a voice memo works
either way. If you have a reverse proxy (Caddy, Nginx Proxy Manager, Traefik), point it at
port 8080 and set `PHONEHOME_COOKIE_SECURE=true`. Reach it
from outside the house over your VPN, never by opening a port.

## Homepage widget

```yaml
- PhoneHome:
    href: http://phonehome.lan:8080
    widget:
      type: customapi
      url: http://phonehome.lan:8080/api/homepage?token=YOUR_HOMEPAGE_TOKEN
      mappings:
        - field: phone
          label: Phone
        - field: new
          label: New
        - field: total
          label: Total
        - field: last
          label: Last
        - field: backup
          label: Backup
        - field: integrity
          label: Files
```

## Backups

The archive on the NAS is the thing to protect. PhoneHome helps three ways:

- **Backup page → Download everything:** one zip with every file and an offline
  `index.html` viewer. Put it on a USB stick or a relative's computer.
- **`tools/backup.ps1`** on your Windows desktop copies the NAS share to an external
  drive: additive, never deletes, and verifies every recording. See
  [tools/README.md](../../tools/README.md). Set `PHONEHOME_BACKUP_TOKEN` so it reports back.
- **A weekly integrity check** re-hashes every recording against `SHA256SUMS`. A problem
  shows on the Backup page, the timeline and the Homepage widget.

The archive folder also keeps its own up-to-date `index.html`, so any copy of it can be
browsed without PhoneHome. Your normal Proxmox `vzdump` covers the database and accounts,
but even without it, `reindex` rebuilds everything except "heard" marks.
