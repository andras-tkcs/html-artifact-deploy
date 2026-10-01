# Deploying HTML Artifact Deploy

This guide sets up one Ubuntu 24.04 machine that runs the server and publishes the pages, with
Caddy providing HTTPS. It uses two DNS names, which you replace with your own:

- `publish.example.com` — this server (sign-in and the MCP endpoint), reverse-proxied by Caddy.
- `pages.example.com` — the folder of published pages, served by Caddy as static files. It must be
  a different host name from the first: a published page runs its own scripts and must not share an
  origin with the server (ADR 0012). The configuration refuses two equal names.

Every command that touches the server's data runs as the service user, `html-artifact-deploy`, so
the files keep one owner. The example files used below are in [`deploy/`](../deploy).

## 1. DNS

Point both names at the machine with an `A` (and, if it has one, `AAAA`) record. Open ports 80 and
443 so Caddy can obtain certificates.

## 2. Create the Google OAuth client

Users sign in with their Google Workspace account. In the Google Cloud console, in the project you
want to use:

1. Open [Branding](https://console.cloud.google.com/auth/branding) and fill in the app name and
   support e-mail.
2. Open [Audience](https://console.cloud.google.com/auth/audience) and choose **Internal**, so only
   accounts of your Workspace organization can sign in.
3. Open [Create client](https://console.cloud.google.com/auth/clients/create), choose **Web
   application**, and add this authorized redirect URI:

   ```
   https://publish.example.com/oauth/google/callback
   ```

4. Keep the **Client ID** (it ends in `.apps.googleusercontent.com`) and the **Client secret**.

## 3. Install Caddy and create the service user and folders

Install Caddy from [Caddy's apt repository](https://caddyserver.com/docs/install#debian-ubuntu-raspbian),
then:

```bash
sudo apt install caddy
sudo useradd --system --home-dir /var/lib/html-artifact-deploy --shell /usr/sbin/nologin html-artifact-deploy
sudo install -d -o html-artifact-deploy -g html-artifact-deploy -m 755 /srv/pages
```

## 4. Install the server

```bash
sudo python3 -m venv /opt/html-artifact-deploy
sudo /opt/html-artifact-deploy/bin/pip install html-artifact-deploy
```

To install a version that is not on PyPI yet, install from the repository instead:
`sudo /opt/html-artifact-deploy/bin/pip install git+https://github.com/andras-tkcs/html-artifact-deploy@<ref>`.

## 5. Write the configuration and the secret

Copy [`deploy/config.example.toml`](../deploy/config.example.toml) and edit it. The keys are
described in [`configuration.md`](configuration.md).

```bash
sudo install -d -m 755 /etc/html-artifact-deploy
sudo install -o root -g html-artifact-deploy -m 0640 deploy/config.example.toml /etc/html-artifact-deploy/config.toml
sudoedit /etc/html-artifact-deploy/config.toml
```

The Google client secret goes in a separate file that only root can read; systemd passes it to the
service as an environment variable:

```bash
sudo install -o root -g root -m 0600 /dev/null /etc/html-artifact-deploy/secrets.env
sudoedit /etc/html-artifact-deploy/secrets.env
```

```
HTML_ARTIFACT_DEPLOY_GOOGLE_CLIENT_SECRET=<the client secret from step 2>
```

## 6. Check the configuration

`check-config` also needs the secret, so run it as the service user with the secrets file loaded:

```bash
sudo systemd-run --pty --wait --uid=html-artifact-deploy \
  -p EnvironmentFile=/etc/html-artifact-deploy/secrets.env \
  /opt/html-artifact-deploy/bin/html-artifact-deploy --config /etc/html-artifact-deploy/config.toml check-config
```

It prints `Configuration OK: /etc/html-artifact-deploy/config.toml`, or says what is wrong.

## 7. Start the server

```bash
sudo install -m 0644 deploy/html-artifact-deploy.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now html-artifact-deploy.service
```

## 8. Configure Caddy

Put the contents of [`deploy/Caddyfile.example`](../deploy/Caddyfile.example), with your host names,
in `/etc/caddy/Caddyfile`, then reload:

```bash
sudo systemctl reload caddy
```

The pages site sends `Content-Security-Policy: sandbox …`, which gives every page an opaque origin.
It does not use `browse`, so no folder is ever listed: the random folder name is what keeps other
pages from being found (ADR 0013).

## 9. Check it

```bash
curl -s https://publish.example.com/healthz
curl -sI https://pages.example.com/
```

The first prints `ok`. The second shows `404` and a `Content-Security-Policy: sandbox …` header.

## 10. Connect clients

See "Connect a client" in the [README](../README.md).

## What a published page can and cannot do

Because of the sandbox header, a page runs with an opaque origin: `localStorage` and cookies do not
persist, and features that exist only inside claude.ai artifacts (shared storage, asking Claude) do
not work once the page is published here.

## Large pages

A page reaches the server in one of three ways. Each has its own limit, and the server refuses a
page over `pages.max_bytes` in every case.

- **As the `html` argument of `publish_page`.** Limited by how much the model can write in one tool
  call, and by `pages.max_bytes` on the server. Fine for small pages.
- **Through `create_page_upload`.** The tool returns a one-time upload address; the client sends the
  file to it with an HTTP `PUT`, then calls `publish_page` with the returned `upload_id`. It needs a
  client that can make an HTTP `PUT`, such as Claude Code; claude.ai cannot. The upload may be at most
  `pages.max_bytes`, the address works for 15 minutes, and one person can have 10 uploads open at a
  time (ADR 0018). It is available over HTTP only, not over stdio.
- **Through `publish_page_from_url`.** The server downloads a page that is already online. It is
  available only when the `[fetch]` section is configured. Only `https://` addresses on the hosts in
  `fetch.allowed_hosts` are fetched, the page may be at most `pages.max_bytes`, and each network
  operation may take `fetch.timeout_seconds`. The list exists because a server that fetches whatever
  address a model hands it can be steered at internal services; it fetches directly, re-checks every
  redirect and refuses private addresses (ADR 0020). Leave `[fetch]` out to turn the tool off.

## Links and expiry

A page's link is `https://pages.example.com/<random id>/`: 128 random bits with no file name or title
in it (ADR 0013). Anyone who has the link can open the page, and the folder is never listed.

Every page expires. A new page stays online for `pages.default_expiry_days` unless its publisher asks
for another number of days, at most `pages.max_expiry_days`. The owner can change the date at any time
with `extend_page_expiry`, again up to `pages.max_expiry_days` days from now. An expired page
disappears from its owner's list at once; its folder is deleted within about an hour (ADR 0021).

The server deletes expired pages after each publish. For quiet periods, install the purge timer, which
runs `purge-expired` every hour:

```bash
sudo install -m 0644 deploy/html-artifact-deploy-purge.service deploy/html-artifact-deploy-purge.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now html-artifact-deploy-purge.timer
```

Check that it is scheduled:

```bash
systemctl list-timers html-artifact-deploy-purge.timer
```

To delete expired pages right away, run `purge-expired` as the service user (see
[`configuration.md`](configuration.md)).
