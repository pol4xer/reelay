# Platform configuration

Reelay uses accounts and developer applications supplied by the operator. Save credentials in
local Reelay Settings or `.env`; never put tokens, downloaded OAuth JSON, or account exports in
issues, pull requests, screenshots, or the source tree.

This guide describes the settings consumed by this implementation. Provider consoles,
permissions, quotas, and app review requirements change; consult the linked official guides
when creating an application. Keep an optional publisher disabled until its account is configured.

## Telegram and the required Instagram destination

Create a Telegram bot with [BotFather](https://core.telegram.org/bots/features#botfather) and
configure these values:

| Variable | Value to supply |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Token issued for your bot |
| `TELEGRAM_OWNER_ID` | Your numeric Telegram user ID, if known |
| `TELEGRAM_OWNER_USERNAME` | Your username, without `@`, for first-time pairing when no owner ID is set |
| `INSTAGRAM_USERNAME` | Your destination Instagram username |
| `META_IG_USER_ID` | Instagram account ID used by the Graph API publisher |
| `META_PAGE_ACCESS_TOKEN` | Page access token authorized for that Instagram account |
| `META_API_VERSION` | Graph API version used by the application |

The Instagram adapter uses Meta's Facebook Graph API flow and a Page access token. Configure the
corresponding Instagram professional account, Facebook Page connection, developer app, and
publishing permissions using Meta's documentation. The Instagram user ID and Facebook Page ID
are distinct values. The settings panel also has fields for app and user credentials useful
while setting up Meta; the publisher itself consumes the account ID and Page token listed above.

Run Reelay and send `/start` from your configured owner's account. Once paired, the numeric owner
ID is stored in SQLite; changing the username in `.env` does not transfer an already paired bot.

Instagram is always included in the current publisher registry. There is no
`PUBLISH_INSTAGRAM=false` mode. Keep all other `PUBLISH_*` flags `false` for the first run.

Official references: [Instagram API with Facebook Login](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-facebook-login/),
[content publishing](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-facebook-login/content-publishing/),
[Meta App Dashboard](https://developers.facebook.com/apps/).

## Facebook Page Reels

The Facebook adapter publishes to a **Page**, using the Page's ID and access token. A personal
profile name or ID is not the destination type implemented here.

1. Choose a Page you manage and configure its publishing access in your Meta developer app.
2. Obtain a Page access token with the permissions required by the Reels publishing flow,
   including `pages_manage_posts`. Confirm that it is authorized for the intended Page.
3. Set `META_PAGE_ID` and `META_PAGE_ACCESS_TOKEN` locally.
4. Set `PUBLISH_FACEBOOK=true` and restart the bot when ready to include Facebook in the queue.

Instagram and Facebook share `META_PAGE_ACCESS_TOKEN` in this implementation. Ensure the chosen
Page token also works for the configured Instagram account before replacing it.

Reference: [Facebook Reels Publishing](https://developers.facebook.com/docs/video-api/guides/reels-publishing/).

## Threads

Create a Threads API application, authorize the intended Threads profile, and grant the access
required for profile identification and content publishing. For development, accept the tester
invitation from that profile if your app setup requires it. Use the **Threads** app credentials,
which can differ from the credentials for your Instagram/Facebook app.

Configure:

| Variable | Purpose |
| --- | --- |
| `THREADS_USER_ID` | Destination profile ID |
| `THREADS_ACCESS_TOKEN` | Authorized token for that profile |
| `THREADS_APP_ID`, `THREADS_APP_SECRET` | Optional setup fields for your Threads application |
| `THREADS_API_VERSION` | Threads API version |
| `PUBLISH_THREADS` | Set to `true` after configuration |

The Threads adapter supplies a `video_url` to the API. For each upload, Reelay:

1. Creates a temporary H.264/AAC MP4 for Threads.
2. Starts a local HTTP server serving only that video.
3. Opens a Cloudflare Quick Tunnel and passes the resulting HTTPS URL to Threads.
4. Waits for processing, publishes the container, and closes the tunnel.
5. Removes temporary media when the operation ends.

Install `cloudflared` on `PATH` or at `data/bin/cloudflared` for a local run; the Docker image
includes it. During processing, this one MP4 is temporarily reachable at the generated public
HTTPS URL through Cloudflare. The tunnel does not expose the settings panel or queue database.

References: [Threads getting started](https://developers.facebook.com/docs/threads/get-started),
[access tokens and permissions](https://developers.facebook.com/docs/threads/get-started/get-access-tokens-and-permissions),
[publishing](https://developers.facebook.com/docs/threads/posts).

## YouTube

The local OAuth helper authorizes a desktop app and confirms the selected channel before saving
its refresh token. It requests `youtube.upload` and `youtube.readonly`; the latter is used to
identify the channel.

1. In [Google Cloud Console](https://console.cloud.google.com/), create or select your project
   and enable YouTube Data API v3.
2. Configure your OAuth consent screen and any test-user access required by the project.
3. Create an OAuth client with the **Desktop app** type.
4. Copy `client_id` and `client_secret` from its downloaded JSON into `YOUTUBE_CLIENT_ID` and
   `YOUTUBE_CLIENT_SECRET` in the local configuration. Keep `PUBLISH_YOUTUBE=false`.
5. Run the helper on the computer where you can complete browser login:

   ```bash
   uv run --no-sync python -m reelay.youtube_oauth
   ```

6. Select the intended account and confirm the displayed channel. The helper atomically saves
   `YOUTUBE_REFRESH_TOKEN` and `YOUTUBE_CHANNEL_ID` without printing their secret values.

If you already know the expected channel ID, require it explicitly:

```bash
uv run --no-sync python -m reelay.youtube_oauth --expected-channel-id YOUR_CHANNEL_ID
```

After restarting Reelay, `/youtube ID` performs a separate **private** test upload for a job with
local media. It does not publish that job to Instagram or consume it from the queue. It is still
an actual API upload. The test ID is stored separately from scheduled publication checkpoints.

To include YouTube in the regular queue, set `PUBLISH_YOUTUBE=true` and choose
`YOUTUBE_PRIVACY_STATUS=private`, `unlisted`, or `public`, then restart. Start with `private`.
The provider can restrict visibility independently of this setting. Short-form classification
is handled by YouTube; this adapter uses the standard video upload API.

If Google returns `invalid_grant`, repeat the local OAuth flow after checking the account's
consent and your project's OAuth status. Move the updated YouTube settings to the deployment and
recreate its container. Preserve credentials for the other platforms. API app reviews and OAuth
consent requirements are separate from Reelay's local checks.

References: [uploading a video](https://developers.google.com/youtube/v3/guides/uploading_a_video),
[installed-app OAuth](https://developers.google.com/youtube/v3/guides/auth/installed-apps),
[quota and compliance audits](https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits).

## TikTok Upload to Inbox

Reelay implements **Upload to Inbox**, not TikTok Direct Post. It uploads the local original MP4
using `FILE_UPLOAD`, waits for `SEND_TO_USER_INBOX`, and saves the returned `publish_id`. The user
then opens the TikTok notification, adds a caption, and publishes manually. Reelay sends the
copyable hashtag line as a separate Telegram message after a new successful Inbox delivery.

1. Create an app in [TikTok for Developers](https://developers.tiktok.com/apps/), configure
   Login Kit and Content Posting API, and authorize an account available to your app's current
   environment.
2. The local helper requests `user.info.basic` and `video.upload`. Configure the desktop login
   redirect URI to match the helper:

   ```text
   http://127.0.0.1:*/callback/
   ```

3. Set `TIKTOK_CLIENT_KEY` and `TIKTOK_CLIENT_SECRET` in local settings. Keep
   `PUBLISH_TIKTOK=false` until authorization is complete.
4. Run:

   ```bash
   make tiktok-oauth
   ```

5. Complete login in the browser and confirm the displayed profile. The helper saves
   `TIKTOK_REFRESH_TOKEN` and `TIKTOK_OPEN_ID` locally without printing secrets.
6. For a server deployment, transfer those configuration values privately. Set
   `PUBLISH_TIKTOK=true` and recreate the container when ready for Inbox delivery.

Reelay stores rotated TikTok refresh tokens in SQLite because a running container's environment
cannot be changed. Use Reelay Settings to update a token and synchronize its runtime state;
editing only `.env` can leave an older SQLite override in effect.

TikTok always receives the original video without Reelay's optional added watermark. Complete
pending Inbox shares in TikTok and observe the limits returned by the API. Sandbox access does
not establish production approval; review and account eligibility are provider-controlled.

References: [Upload to Inbox](https://developers.tiktok.com/doc/content-posting-api-get-started-upload-content/),
[desktop Login Kit](https://developers.tiktok.com/doc/login-kit-desktop/),
[OAuth token management](https://developers.tiktok.com/doc/oauth-user-access-token-management/).

## Applying configuration changes

A foreground process must be restarted after `.env` changes. The local panel's restart action
manages the macOS LaunchAgent. Docker needs a recreated container:

```bash
docker compose up --detach --force-recreate reelay
```

`docker compose restart` keeps the old environment. Keep the existing data directory so saved
owner identity, schedule overrides, token rotations, and successful platform IDs remain available.
Failures on one platform do not prevent attempts on the others; after fixing access, `/retry ID`
requeues only the unfinished deliveries for that job.
