# Watchdog operations

## Start with a dry run

Install `requirements.txt` in a Python 3.12 virtual environment and run
`python alerts.py --dry-run`. Reports are written even when no threshold is met.
Exit 0 means evaluation completed; exit 2 signals a request/configuration/output
failure, an unavailable regional evaluation, or a failed/uncertain primary send. A
missing CallMeBot key is a skipped optional channel, not an error. ntfy failure
is reported but does not fail a Gmail-enabled run; an ntfy-only delivery failure
still exits 2. The
reports retain missing fields, incomplete horizons and warnings.

`--send` permits qualifying scheduled notifications; `--test-email` permits
one explicit Gmail delivery test. Both validate their credentials before fetching. For scheduled sends, recorded data older than two hours or future-dated by more
than five minutes is refused; `--at` is dry-run only. Notification submissions
are not automatically retried within a run. Accepted means provider acceptance,
not handset/inbox receipt.

## GitHub configuration

Put all files and `.github/workflows/soaring_watchdog.yml` on the default branch.
In repository **Settings → Secrets and variables → Actions**:

For **Gmail plus free ntfy phone alerts**, the only required repository secret is
**`GMAIL_APP_PASSWORD`**. Spaces and other whitespace are stripped from Google's
grouped app password before SMTP login. Use the sending Gmail account's app
password, not its ordinary login password. Leave **`CALLMEBOT_API_KEY` unset**
while registration is unavailable: WhatsApp is skipped before validation or
ledger claims, and Gmail/ntfy continue. A missing CallMeBot key cannot cause exit 2.

Keep sender, recipient, channel and topic settings in the **Variables** tab.
If `DUO_ALERT_CHANNELS` already exists, include `ntfy` to receive phone pushes;
unset it to use the default `email,ntfy,whatsapp`. Use `email` for Gmail only or
`ntfy` for keyless push only. Twilio credentials are required only when Twilio
is explicitly selected as an active WhatsApp provider.

GitHub supplies `github.token` automatically for workflow operations; neither
workflow needs a manually created GitHub token secret. Weather map lookups,
including Open-Meteo point precipitation, need no API key.

| Kind | Name | Value/use |
| --- | --- | --- |
| Secret | `GMAIL_APP_PASSWORD` | Gmail app password for the sending account |
| Variable | `SMTP_USERNAME` | Sender, defaults to manorhousesurveyors@gmail.com |
| Variable | `DUO_EMAIL_TO` | Recipient, defaults to manorhousesurveyors@gmail.com |
| Variable | `DUO_ALERT_CHANNELS` | `email,ntfy,whatsapp` default; comma-separated channel selection |
| Variable | `DUO_NTFY_TOPIC` | Defaults to `g-ckml-soaring-alerts`; no ntfy secret required |
| Variable | `DUO_WHATSAPP_TO` | E.164 recipient, defaults to +447976125887 |
| Variable | `WHATSAPP_PROVIDER` | `callmebot` default, or `twilio` |
| Secret | `CALLMEBOT_API_KEY` | Optional recipient-authorized key; blank/unset skips CallMeBot |
| Secret | `TWILIO_ACCOUNT_SID` | Twilio account SID, when selected |
| Secret | `TWILIO_AUTH_TOKEN` | Twilio auth token, when selected |
| Variable | `TWILIO_WHATSAPP_FROM` | Approved sender in `whatsapp:+number` format |
| Variable | `TWILIO_CONTENT_SID` | Approved WhatsApp content-template SID (`HX…`) |

Twilio messages outside a 24-hour user-initiated window need an approved
template. Configure its `{{1}}` variable for the briefing and verify its
approval/length restrictions with Twilio. The sender supplies `ContentSid`
and `ContentVariables`, not free-form `Body`. CallMeBot requires recipient
opt-in/key and offers no delivery guarantee. Gmail requires app-password
support. Keep credentials in the secret store, never in files, logs or chat.

Twilio's variable is flattened to one line and limited to 900 characters to
leave room for the approved template's fixed text within its body limit.

Run manually with **send = false** to inspect dry-run HTML briefings. Choose
**send = true** to send one immediate test email to **manorhousesurveyors@gmail.com**
with five dated Long Mynd star-rating rows (today and the following four days).
This explicitly requested test bypasses weather thresholds and earlier sends,
uses Gmail only, and does not change the scheduled-alert ledger. Each manual
invocation, including a rerun, requests a new test email. It overrides a different
`DUO_EMAIL_TO` for this test and still uses `SMTP_USERNAME` as the sender.

The test fetches one ECMWF Long Mynd forecast instead of the national grid.
Unavailable or stale weather produces labelled unavailable star rows and still
allows the delivery test; it never invents ratings. Requests include
`past_hours=24` so today's morning window is present, including BST/GMT changes.
Provider gaps can still leave a rating unavailable; missing data is not zero.
Reports are `briefing-test-email.html` and `.json`, with provider acceptance or
failure recorded separately. Gmail acceptance returns 0; SMTP rejection or an
uncertain send remains a delivery error. The command-line equivalent is
`python alerts.py --test-email`. These tests do not send WhatsApp/ntfy messages.

Scheduled runs at `17 */6 * * *`
UTC send automatically. GitHub may delay jobs and disable inactive public-repo
schedules; monitor Actions. The workflow is limited to the default branch,
uses pinned actions and read-only permissions, and serializes jobs with
`cancel-in-progress: false`.

## Free ntfy phone alerts

Install the free **ntfy** app, allow notifications, then subscribe to topic
**`g-ckml-soaring-alerts`** on **`https://ntfy.sh`**. The direct topic is
<https://ntfy.sh/g-ckml-soaring-alerts>. No API key is required. This unreserved
topic is public; anyone who knows its name can read or publish messages.
Messages contain a short soaring briefing, with details in Gmail.

Pushes are sent only for qualifying triggers with `--send`, using the same
per-date/horizon deduplication as email. Acceptance requires ntfy's JSON message
ID and matching topic. TLS remains verified, redirects are refused, and uncertain
POST outcomes are held for reconciliation rather than blindly retried.
ntfy is best-effort alongside Gmail: its errors stay visible in delivery results
and the ledger while successful email runs can exit 0. Provider acceptance does
not establish phone receipt or override the phone's notification settings.

Both workflows now use SHA-pinned **Node 24** actions: checkout v5, setup-python
v6, github-script v8, upload-artifact v6 and download-artifact v7 (where used).
Merely moving all actions to v4 would retain older runtimes. The pinned manifests
were checked for `runs.using: node24`; GitHub-hosted `ubuntu-latest` supplies a
compatible runner. Artifact restore inputs and durable-state behavior are retained.

## Durable state and recovery

The WAL SQLite ledger is `var/duo_discus.sqlite3`. Discovery searches retained
artifacts belonging to this workflow/default branch, including older failed
runs and the current run's earlier attempts. A later failed run without an
artifact does not hide an earlier backup. The newest available candidate is
downloaded and verified for integrity, schema, columns and audit references.

If discovery or download fails, or the candidate is missing, expired, corrupt or
incompatible, `scripts/watchdog_state.py prepare` logs the reason and initializes
a valid empty SQLite ledger—the application's equivalent of `{}`. These
recovery cases exit 0 and permit the job to continue. Invalid downloaded files
remain untouched; invalid local working state is moved to `rejected-state/`.
The alert CLI also recovers an invalid local ledger. Recreating the ledger
forgets earlier sends and can repeat alerts. Installation, forecast, filesystem
and real delivery errors retain their normal failure reporting.

Schema 2 adds the ntfy channel. The workflow first verifies schema-1 artifacts
read-only, keeps the restored copy, then migrates the working ledger atomically.
It retains every delivery ID, status, token, provider ID, audit event and reference
profile. Previously sent or uncertain email/WhatsApp records still suppress
duplicates. Local `DatabaseManager` performs the same migration when opened.

After state validation, an `always()` step makes a consistent SQLite backup,
including WAL transactions, and uploads it for 90 days even if installation,
tests or delivery fail. Reports are retained 14 days. Organization/repository
retention limits can shorten this period. Artifacts include recipient addresses
and forecast results; repository access controls apply. Weather bundles remain
local runtime files and are not uploaded by the workflow.

Deduplication key: target **London local date**, 2/5-day horizon, `briefing`,
`national`, channel and canonical recipient. A changed payload/provider does not
bypass it. Each channel independently succeeds or retries a confirmed rejection.
After acceptance, later conditions for that date/horizon appear only in reports;
there is no second notification.

The ledger reserves an alert, records `sending` before submission, then marks
`sent`, `failed` or `uncertain`. Expired unstarted reservations can be reclaimed.
Started/uncertain sends are held until an operator verifies the outcome. This
favors avoiding duplicates over automatically recovering ambiguous sends.

Inspect/reconcile a downloaded database locally:

```python
from database import DatabaseManager
db = DatabaseManager('var/duo_discus.sqlite3')
for row in db.list_alerts():
    print(row['id'], row['status'], row['forecast_date'], row['channel'])
# After the old sender stops and provider evidence is checked:
# db.reconcile_delivery(alert_id, delivered=True, evidence='Provider accepted ID …')
# delivered=False permits retry; use only when confirmed not delivered.
```

Manual input `restore_run_id` prefers a retained artifact from that run in this
workflow/default branch. An invalid run ID falls back to automatic discovery;
an unavailable or invalid artifact falls back to an empty ledger. Recover a
known backup and reconcile later deliveries when continuity matters. Older or
empty state can duplicate alerts. Keep an independent
backup for long shutdowns. SMTP/WhatsApp, SQLite and artifact upload cannot share
one atomic transaction, so exactly-once delivery is not guaranteed. Cancellation
before backup also requires review.

A persistent VM can instead use:

```cron
17 */6 * * * cd /srv/duo-discus-weather && .venv/bin/python alerts.py --send >> var/watchdog.log 2>&1
```

Provision secrets through the service environment, retain SQLite, configure log
rotation and avoid overlapping schedulers. Do not run VM and GitHub schedules
against separate ledgers for the same recipients.

## API quota and networking

Open-Meteo batches reduce HTTP overhead, not weighted API cost. Pacing uses
coordinate count × max(1, hourly variables × models / 10 × max(1, days/14)),
with a default 300 weighted calls/minute. A 429 without `Retry-After` causes a
shared 60-second cooldown. Caches/limiters are per process; dashboard sessions
and runners behind one IP can share the upstream quota without sharing the
limiter. Lower `DUO_OPEN_METEO_CALLS_PER_MINUTE` when necessary.

The expanded watchdog costs roughly 1,200 weighted calls/run, around 4,800/day,
before retries/dashboard usage. Consult [Open-Meteo pricing](https://open-meteo.com/en/pricing)
for the free tier's usage/license terms. The 195-node wind display adds about
566 weighted calls per cold model request and uses a 15-minute cache. Retain the 0.5° convergence grid unless budget and
provider plan support more points.

Weather HTTPS hosts: `api.open-meteo.com`, `aviationweather.gov`.
Map HTTPS hosts: `api.rainviewer.com`, `tilecache.rainviewer.com`,
`tile.openstreetmap.org`, `a.tile.opentopomap.org`, `cdn.jsdelivr.net`,
`server.arcgisonline.com`. Terrain refresh uses `s3.amazonaws.com`.
OGN uses `live.glidernet.org` and `ddb.glidernet.org` over verified HTTPS;
no APRS TCP port, API key or new GitHub secret is required.
Notification HTTPS hosts: `ntfy.sh`, plus optional `api.callmebot.com` or `api.twilio.com`.
Email uses `smtp.gmail.com` **TCP 465**, not HTTPS. Keep certificate verification
enabled; an HTTPS credential proxy cannot inject SMTP passwords. Credentials
are ordinary secure runtime environment values on the delivery host. Preserve
map attribution and observe provider usage policies. Avoid bulk tile downloads;
use a hosted tile service if deployment traffic exceeds public-service limits.

## Dashboard and cloud startup

Run `.venv/bin/python -m streamlit run app.py` from the checkout. Configuration
binds to loopback port 8501 with analytics disabled. Use an authenticated TLS
reverse proxy for external hosting. Weather browsing needs no alert credentials.

Check local `/_stcore/health`, then verify map/metrics/inspector; HTTP health alone
does not prove weather/tile access. Data quality shows ages and unavailable
fields. Radar has its own observed timestamp, independent of forecast sliders.
Snapshots are labelled and never silently substituted after an outage.

Cloud setup saves install/start instructions and domains in the configuration
draft. Review/save in environment settings and publish to apply it. GitHub
schedule activation and secret provisioning are separate deployment steps.


Airfield source snapshots additionally use `raw.githubusercontent.com` and the
NOAA station cache. Runtime airfield reports use the existing Open-Meteo/NOAA
hosts. UK airspace snapshots were verified from `asselect.uk` and the publisher's
`ahsparrow/airspace` GitHub repository. The new `ntfy.sh` entry was saved to the
cloud network draft and requires publication for cloud connectivity. GitHub-hosted
Actions have their own network access; that draft does not configure GitHub Secrets.


## Star alerts and live traffic

A supported three-, four- or five-star region is an additional alert trigger.
Email includes the stars in its subject/body and high-priority headers; ntfy
uses `Priority: high`. The existing per-day/horizon delivery ledger still
suppresses duplicates. A later rating upgrade does not resend an accepted alert.

OGN uses one daemon worker per Streamlit process, with a 20-second feed cadence
and a 24-hour device registry cache. It writes `static/ogn/uk.json` atomically;
`server.enableStaticServing = true` must remain enabled. Browser requests use
the app's own `/app/static/ogn/uk.json` path, including a configured URL prefix.
The static file contains only the filtered current public reports, not a flight
history. Both static snapshots and `var/ogn-devices.json` are ignored by Git.

Bundled wave terrain needs no elevation download during normal map use.
To reproduce or refresh it, run `.venv/bin/python scripts/update_continuous_terrain.py`.
Inspect the metadata/hashes and rerun tests before committing the new archive.
This fetches the bounded set of zoom-8 elevation tiles recorded in the metadata.
The older `update_wave_terrain.py` reproduces the retained 500 m reference grids.


## Lazy pages and local place lookup

The active page is stored in `active_page`. Weather/API bundles, day summaries,
task assessments and wave products use 30-minute caches. Gemini discovery uses a
one-hour cache keyed by a credential fingerprint and authentication type. Runtime
credentials remain only in server session memory; they are absent from map
payloads. Static catalogs and terrain preparation use shared resource caches.
Restart the server after replacing the static airfield or DEM files.

UK town lookup needs HTTPS access to `geocoding-api.open-meteo.com`. Its domain
was saved to the cloud network draft on 24 October 2026. Subsequent verified HTTPS
checks resolved Birmingham and Church Stretton successfully. A new environment
with a custom allowlist must include that domain. BGA/airfield lookup and
map-coordinate selection continue without geocoding. Geocoding uses UK-only results and reports
ambiguous names instead of picking an unverified location.

Only the dedicated Live OGN Glider Radar page renews the provider worker's
35-second activity lease. Leaving that page pauses it; closing the browser lets
the lease expire. Other maps may display the last available traffic snapshot,
with its existing stale-report filtering. Recorded/demo sessions never start a
live worker. Browser polling and real provider access are separate operations.

Run `scripts/verify_lazy_pages.py`, `scripts/verify_route_drag.py` and
`scripts/verify_client_maps.py --proxy-assets` against the recorded test server
on port 8504. `scripts/verify_gemini_browser.py` uses the explicitly mocked fixture
server on port 8505 and cannot verify real Google quota or account access.
