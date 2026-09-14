import re
from copy import deepcopy
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .scheduler import parse_post_times


def _field(
    key,
    section,
    label,
    *,
    field_type="text",
    secret=False,
    required=False,
    help_text="",
    placeholder="",
    options=None,
    link=None,
    depends_on=None,
    default="",
):
    field = {
        "key": key,
        "section": section,
        "label": label,
        "title": label,
        "type": field_type,
        "secret": secret,
        "required": required,
        "help": help_text,
        "placeholder": placeholder,
        "default": default,
    }
    if options is not None:
        field["options"] = options
    if link is not None:
        field["link"] = link
    if depends_on is not None:
        field["depends_on"] = depends_on
    return field


CONFIG_SCHEMA = (
    _field(
        "TELEGRAM_BOT_TOKEN",
        "telegram",
        "Bot token",
        field_type="password",
        secret=True,
        required=True,
        help_text="Bot token issued by BotFather.",
        placeholder="123456789:AA…",
        link="https://t.me/BotFather",
    ),
    _field(
        "TELEGRAM_OWNER_ID",
        "telegram",
        "Telegram owner ID",
        field_type="number",
        help_text="Your numeric Telegram user ID (recommended). Or set a username below to bind it with /start.",
        placeholder="123456789",
    ),
    _field(
        "TELEGRAM_OWNER_USERNAME",
        "telegram",
        "Telegram username",
        help_text="Your username without @, used only until your numeric owner ID is saved.",
        placeholder="your_username",
    ),
    _field(
        "META_APP_ID",
        "instagram_meta",
        "Meta App ID",
        help_text="Meta app ID for the Instagram and Facebook Graph APIs.",
        placeholder="123456789012345",
        link="https://developers.facebook.com/apps/",
    ),
    _field(
        "META_APP_SECRET",
        "instagram_meta",
        "Meta App Secret",
        field_type="password",
        secret=True,
        help_text="Meta app secret. Leave blank to keep the saved value.",
        placeholder="••••••••••••••••",
        link="https://developers.facebook.com/apps/",
    ),
    _field(
        "META_IG_USER_ID",
        "instagram_meta",
        "Instagram professional account ID",
        required=True,
        help_text="Numeric professional Instagram account ID from the Meta Graph API.",
        placeholder="17841400000000000",
        link="https://developers.facebook.com/docs/instagram-platform/instagram-api-with-facebook-login/get-started",
    ),
    _field(
        "META_PAGE_ACCESS_TOKEN",
        "instagram_meta",
        "Meta Page access token",
        field_type="password",
        secret=True,
        required=True,
        help_text="Long-lived Page token for publishing to Instagram and Facebook.",
        placeholder="EAAB…",
        link="https://developers.facebook.com/tools/explorer/",
    ),
    _field(
        "META_API_VERSION",
        "instagram_meta",
        "Meta API version",
        required=True,
        help_text="Graph API version, such as v26.0.",
        placeholder="v26.0",
        default="v26.0",
        link="https://developers.facebook.com/docs/graph-api/changelog/",
    ),
    _field(
        "INSTAGRAM_USERNAME",
        "instagram_meta",
        "Instagram username",
        required=True,
        help_text="Target Instagram account username without @.",
        placeholder="your_instagram_account",
        link="https://www.instagram.com/accounts/edit/",
    ),
    _field(
        "CHROME_PROFILE",
        "instagram_meta",
        "Chrome profile",
        help_text="Chrome profile to use when private source access is enabled.",
        placeholder="Default",
        default="Default",
    ),
    _field(
        "ALLOW_PRIVATE_SOURCES",
        "instagram_meta",
        "Allow private sources",
        field_type="toggle",
        help_text="Allow the downloader to read cookies from the selected Chrome profile.",
        default="false",
    ),
    _field(
        "PUBLISH_FACEBOOK",
        "facebook",
        "Publish to Facebook",
        field_type="toggle",
        required=True,
        help_text="Include Facebook Page Reels in scheduled publishing.",
        default="false",
        link="https://developers.facebook.com/docs/video-api/guides/reels-publishing/",
    ),
    _field(
        "META_PAGE_ID",
        "facebook",
        "Facebook Page ID",
        help_text="Numeric Page ID. Required when Facebook publishing is enabled.",
        placeholder="123456789012345",
        depends_on="PUBLISH_FACEBOOK",
        link="https://developers.facebook.com/tools/explorer/",
    ),
    _field(
        "META_USER_ACCESS_TOKEN",
        "facebook",
        "Meta User access token",
        field_type="password",
        secret=True,
        help_text="User token for obtaining a long-lived Page token; not used for publishing.",
        placeholder="EAAB…",
        link="https://developers.facebook.com/tools/explorer/",
    ),
    _field(
        "PUBLISH_THREADS",
        "threads",
        "Publish to Threads",
        field_type="toggle",
        required=True,
        help_text="Include Threads video posts in scheduled publishing.",
        default="false",
        link="https://developers.facebook.com/docs/threads/",
    ),
    _field(
        "THREADS_API_VERSION",
        "threads",
        "Threads API version",
        required=True,
        help_text="Threads API version, such as v1.0.",
        placeholder="v1.0",
        default="v1.0",
        link="https://developers.facebook.com/docs/threads/changelog/",
    ),
    _field(
        "THREADS_APP_ID",
        "threads",
        "Threads App ID",
        help_text="App ID with the Threads API use case.",
        placeholder="123456789012345",
        depends_on="PUBLISH_THREADS",
        link="https://developers.facebook.com/apps/",
    ),
    _field(
        "THREADS_APP_SECRET",
        "threads",
        "Threads App Secret",
        field_type="password",
        secret=True,
        help_text="Threads app secret. Leave blank to keep the saved value.",
        placeholder="••••••••••••••••",
        depends_on="PUBLISH_THREADS",
        link="https://developers.facebook.com/apps/",
    ),
    _field(
        "THREADS_USER_ID",
        "threads",
        "Threads User ID",
        help_text="Numeric Threads user ID. Required when Threads publishing is enabled.",
        placeholder="123456789012345",
        depends_on="PUBLISH_THREADS",
        link="https://developers.facebook.com/docs/threads/get-started/",
    ),
    _field(
        "THREADS_ACCESS_TOKEN",
        "threads",
        "Threads access token",
        field_type="password",
        secret=True,
        help_text="Long-lived token with threads_basic and threads_content_publish permissions.",
        placeholder="THQ…",
        depends_on="PUBLISH_THREADS",
        link="https://developers.facebook.com/docs/threads/get-started/get-access-tokens-and-permissions",
    ),
    _field(
        "PUBLISH_YOUTUBE",
        "youtube",
        "Publish to YouTube",
        field_type="toggle",
        required=True,
        help_text="Include YouTube Shorts in scheduled publishing.",
        default="false",
        link="https://console.cloud.google.com/apis/library/youtube.googleapis.com",
    ),
    _field(
        "YOUTUBE_CLIENT_ID",
        "youtube",
        "OAuth client ID",
        help_text="Desktop app OAuth client ID from Google Cloud Console.",
        placeholder="1234-abc.apps.googleusercontent.com",
        depends_on="PUBLISH_YOUTUBE",
        link="https://console.cloud.google.com/apis/credentials",
    ),
    _field(
        "YOUTUBE_CLIENT_SECRET",
        "youtube",
        "OAuth client secret",
        field_type="password",
        secret=True,
        help_text="Desktop app OAuth client secret. Leave blank to keep the saved value.",
        placeholder="GOCSPX-…",
        depends_on="PUBLISH_YOUTUBE",
        link="https://console.cloud.google.com/apis/credentials",
    ),
    _field(
        "YOUTUBE_REFRESH_TOKEN",
        "youtube",
        "OAuth refresh token",
        field_type="password",
        secret=True,
        help_text="Refresh token obtained by running YouTube OAuth setup.",
        placeholder="1//…",
        depends_on="PUBLISH_YOUTUBE",
        link="https://developers.google.com/youtube/v3/guides/auth/installed-apps",
    ),
    _field(
        "YOUTUBE_CHANNEL_ID",
        "youtube",
        "YouTube channel ID",
        help_text="ID of the channel verified during OAuth, usually starting with UC.",
        placeholder="UCxxxxxxxxxxxxxxxxxxxxxx",
        depends_on="PUBLISH_YOUTUBE",
        link="https://www.youtube.com/account_advanced",
    ),
    _field(
        "YOUTUBE_PRIVACY_STATUS",
        "youtube",
        "Default privacy",
        field_type="select",
        required=True,
        help_text="Privacy setting for new YouTube uploads.",
        options=(
            {"value": "private", "label": "Private"},
            {"value": "unlisted", "label": "Unlisted"},
            {"value": "public", "label": "Public"},
        ),
        default="private",
        link="https://support.google.com/youtube/contact/yt_api_form",
    ),
    _field(
        "PUBLISH_TIKTOK",
        "tiktok",
        "Send to TikTok Inbox",
        field_type="toggle",
        required=True,
        help_text=(
            "Upload MP4 files to TikTok Inbox. After the notification, manually "
            "add a caption and tap Publish."
        ),
        default="false",
        link="https://developers.tiktok.com/docs/en/content-posting-api-get-started-upload-content",
    ),
    _field(
        "TIKTOK_CLIENT_KEY",
        "tiktok",
        "Client key",
        help_text="App client key from TikTok for Developers.",
        placeholder="awxxxxxxxxxxxxxxxx",
        depends_on="PUBLISH_TIKTOK",
        link="https://developers.tiktok.com/apps/",
    ),
    _field(
        "TIKTOK_CLIENT_SECRET",
        "tiktok",
        "Client secret",
        field_type="password",
        secret=True,
        help_text="App client secret. Leave blank to keep the saved value.",
        placeholder="••••••••••••••••",
        depends_on="PUBLISH_TIKTOK",
        link="https://developers.tiktok.com/apps/",
    ),
    _field(
        "TIKTOK_REFRESH_TOKEN",
        "tiktok",
        "OAuth refresh token",
        field_type="password",
        secret=True,
        help_text="Refresh token obtained by running TikTok OAuth setup.",
        placeholder="rft.…",
        depends_on="PUBLISH_TIKTOK",
        link="https://developers.tiktok.com/docs/en/oauth-user-access-token-management",
    ),
    _field(
        "TIKTOK_OPEN_ID",
        "tiktok",
        "Authorized account Open ID",
        help_text="Open ID of the TikTok profile verified during OAuth.",
        placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
        depends_on="PUBLISH_TIKTOK",
        link="https://developers.tiktok.com/docs/en/login-kit-desktop",
    ),
    _field(
        "TIKTOK_REDIRECT_URI",
        "tiktok",
        "Desktop redirect URI",
        required=True,
        help_text=(
            "Add this URI to Login Kit Desktop. The wildcard port lets "
            "OAuth setup choose an available local port."
        ),
        placeholder="http://127.0.0.1:*/callback/",
        default="http://127.0.0.1:*/callback/",
        link="https://developers.tiktok.com/docs/en/login-kit-desktop",
    ),
    _field(
        "TIMEZONE",
        "schedule_storage",
        "Timezone",
        required=True,
        help_text="IANA timezone used to calculate your publishing schedule.",
        placeholder="UTC",
        default="UTC",
        link="https://en.wikipedia.org/wiki/List_of_tz_database_time_zones",
    ),
    _field(
        "POSTS_PER_DAY",
        "schedule_storage",
        "Posts per day",
        field_type="number",
        required=True,
        help_text="From 1 to 12 evenly spaced slots within the publishing window.",
        placeholder="5",
        default="5",
    ),
    _field(
        "POST_TIMES",
        "schedule_storage",
        "Exact publish times",
        help_text=(
            "Optional comma-separated list of exact publishing times. "
            "Overrides the daily count and publishing window when set."
        ),
        placeholder="13:00,18:30,21:30",
    ),
    _field(
        "POST_WINDOW_START",
        "schedule_storage",
        "Publish window starts",
        field_type="time",
        required=True,
        help_text="Publishing window start in HH:MM format.",
        placeholder="09:00",
        default="09:00",
    ),
    _field(
        "POST_WINDOW_END",
        "schedule_storage",
        "Publish window ends",
        field_type="time",
        required=True,
        help_text="Publishing window end. Overnight windows are supported.",
        placeholder="21:00",
        default="21:00",
    ),
    _field(
        "SCHEDULE_GRACE_MINUTES",
        "schedule_storage",
        "Catch-up grace, minutes",
        field_type="number",
        required=True,
        help_text="How long after a scheduled time one missed slot can be caught up.",
        placeholder="30",
        default="30",
    ),
    _field(
        "POST_ON_WEEKENDS",
        "schedule_storage",
        "Post on weekends",
        field_type="toggle",
        required=True,
        help_text="Allow scheduled publishing on Saturdays and Sundays.",
        default="true",
    ),
    _field(
        "CAPTION_MODE",
        "schedule_storage",
        "Caption mode",
        field_type="select",
        required=True,
        help_text="How captions are generated for queued videos.",
        options=({"value": "message_or_empty", "label": "Message or empty"},),
        default="message_or_empty",
    ),
    _field(
        "SEND_MP4_AUTOMATICALLY",
        "schedule_storage",
        "Send MP4 automatically",
        field_type="toggle",
        required=True,
        help_text="Automatically send downloaded MP4 files to the owner in Telegram.",
        default="false",
    ),
    _field(
        "DELETE_AFTER_PUBLISH",
        "schedule_storage",
        "Delete after publishing",
        field_type="toggle",
        required=True,
        help_text="Delete the local MP4 after successful publishing to every enabled platform.",
        default="true",
    ),
    _field(
        "VIDEO_WATERMARK_ENABLED",
        "schedule_storage",
        "Reelay watermark",
        field_type="toggle",
        required=True,
        help_text=(
            "Add a small translucent Reelay logo to Instagram, Facebook, Threads "
            "and YouTube. TikTok always receives the original without a watermark."
        ),
        default="true",
    ),
    _field(
        "AUTO_TAGS",
        "schedule_storage",
        "Generate hashtags",
        field_type="toggle",
        required=True,
        help_text="Automatically detect the video topic and generate hashtags.",
        default="true",
    ),
    _field(
        "AUTO_TAG_MIN",
        "schedule_storage",
        "Minimum hashtags",
        field_type="number",
        required=True,
        help_text="Minimum hashtags, from 0 to 30 and no greater than the maximum.",
        placeholder="15",
        default="15",
    ),
    _field(
        "AUTO_TAG_MAX",
        "schedule_storage",
        "Maximum hashtags",
        field_type="number",
        required=True,
        help_text="Maximum hashtags, from 0 to 30 and no less than the minimum.",
        placeholder="20",
        default="20",
    ),
)

FIELD_BY_KEY = {field["key"]: field for field in CONFIG_SCHEMA}
ALLOWED_KEYS = frozenset(FIELD_BY_KEY)
SECRET_KEYS = frozenset(field["key"] for field in CONFIG_SCHEMA if field["secret"])
BOOLEAN_KEYS = frozenset(field["key"] for field in CONFIG_SCHEMA if field["type"] == "toggle")
INTEGER_RANGES = {
    "TELEGRAM_OWNER_ID": (1, 9_223_372_036_854_775_807),
    "POSTS_PER_DAY": (1, 12),
    "SCHEDULE_GRACE_MINUTES": (1, 1_440),
    "AUTO_TAG_MIN": (0, 30),
    "AUTO_TAG_MAX": (0, 30),
}
DEFAULTS = {field["key"]: str(field["default"]) for field in CONFIG_SCHEMA}
API_VERSION = re.compile(r"^v[1-9][0-9]*(?:\.[0-9]+)?$")
CLOCK_TIME = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
USERNAME = re.compile(r"^[A-Za-z0-9._]{1,64}$")
TELEGRAM_USERNAME = re.compile(r"^[A-Za-z0-9_]{1,64}$")


def public_schema():
    return deepcopy(CONFIG_SCHEMA)


def effective_values(stored):
    return {key: str(stored.get(key, DEFAULTS[key]) or "") for key in ALLOWED_KEYS}


def normalize_updates(values):
    if not isinstance(values, dict):
        return {}, {"values": "Expected an object containing the changed settings."}

    normalized = {}
    errors = {}
    for key, value in values.items():
        if key not in ALLOWED_KEYS:
            errors[key] = "Unknown setting."
            continue
        if key in BOOLEAN_KEYS:
            if isinstance(value, bool):
                normalized[key] = "true" if value else "false"
                continue
            if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
                normalized[key] = value.strip().lower()
                continue
            errors[key] = "Enter true or false."
            continue
        if key in INTEGER_RANGES:
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                errors[key] = "Enter a whole number."
                continue
            raw_value = str(value).strip()
            if key == "TELEGRAM_OWNER_ID" and not raw_value:
                normalized[key] = ""
                continue
            try:
                normalized[key] = str(int(raw_value, 10))
            except ValueError:
                errors[key] = "Enter a whole number."
            continue
        if key == "POST_TIMES":
            if not isinstance(value, str):
                errors[key] = "Enter times as comma-separated text."
                continue
            try:
                normalized[key] = ",".join(parse_post_times(value.strip()))
            except ValueError:
                errors[key] = (
                    "Enter 1–12 unique HH:MM times separated by commas in ascending order."
                )
            continue
        if not isinstance(value, str):
            errors[key] = "Enter a text value."
            continue
        value = value.strip()
        if len(value) > 16_384:
            errors[key] = "The value is too long."
            continue
        if any(character in value for character in ("\x00", "\r", "\n")):
            errors[key] = "Line breaks and NUL characters are not allowed."
            continue
        if "${" in value:
            errors[key] = "The ${…} sequence is not supported in .env values."
            continue
        if key in SECRET_KEYS and not value:
            continue
        if key in {"TELEGRAM_OWNER_USERNAME", "INSTAGRAM_USERNAME"}:
            value = value.lstrip("@").strip()
        normalized[key] = value
    return normalized, errors


def validate_values(values):
    values = effective_values(values)
    errors = {}

    for field in CONFIG_SCHEMA:
        key = field["key"]
        if field["required"] and not values[key]:
            errors[key] = "This field is required."

    conditional = {
        "PUBLISH_FACEBOOK": ("META_PAGE_ID",),
        "PUBLISH_THREADS": (
            "THREADS_APP_ID",
            "THREADS_APP_SECRET",
            "THREADS_USER_ID",
            "THREADS_ACCESS_TOKEN",
        ),
        "PUBLISH_YOUTUBE": (
            "YOUTUBE_CLIENT_ID",
            "YOUTUBE_CLIENT_SECRET",
            "YOUTUBE_REFRESH_TOKEN",
            "YOUTUBE_CHANNEL_ID",
        ),
        "PUBLISH_TIKTOK": (
            "TIKTOK_CLIENT_KEY",
            "TIKTOK_CLIENT_SECRET",
            "TIKTOK_REFRESH_TOKEN",
            "TIKTOK_OPEN_ID",
        ),
    }
    for flag, keys in conditional.items():
        if values[flag] == "true":
            for key in keys:
                if not values[key]:
                    errors[key] = f"Required when {flag}=true."

    for key in BOOLEAN_KEYS:
        if values[key] not in {"true", "false"}:
            errors[key] = "Only true or false are allowed."

    for key, (minimum, maximum) in INTEGER_RANGES.items():
        value = values[key]
        if key == "TELEGRAM_OWNER_ID" and not value:
            continue
        try:
            number = int(value, 10)
        except ValueError:
            errors[key] = "Enter a whole number."
            continue
        if not minimum <= number <= maximum:
            errors[key] = f"Allowed range: {minimum}–{maximum}."

    for key in ("POST_WINDOW_START", "POST_WINDOW_END"):
        if values[key] and not CLOCK_TIME.fullmatch(values[key]):
            errors[key] = "Use HH:MM format."
    if values["POST_TIMES"]:
        try:
            parse_post_times(values["POST_TIMES"])
        except ValueError:
            errors["POST_TIMES"] = (
                "Enter 1–12 unique HH:MM times separated by commas in ascending order."
            )
    if (
        not values["POST_TIMES"]
        and values["POSTS_PER_DAY"] != "1"
        and values["POST_WINDOW_START"]
        and values["POST_WINDOW_START"] == values["POST_WINDOW_END"]
    ):
        errors["POST_WINDOW_END"] = "The window must have a nonzero duration for multiple posts."

    for key in ("META_API_VERSION", "THREADS_API_VERSION"):
        if values[key] and not API_VERSION.fullmatch(values[key]):
            errors[key] = "Use a version format such as v26.0."

    redirect_uri = values["TIKTOK_REDIRECT_URI"]
    if redirect_uri:
        parseable = redirect_uri.replace(":*", ":1", 1)
        try:
            parsed_redirect = urlsplit(parseable)
            redirect_port = parsed_redirect.port
        except ValueError:
            parsed_redirect = None
            redirect_port = None
        if (
            parsed_redirect is None
            or parsed_redirect.scheme != "http"
            or parsed_redirect.hostname not in {"127.0.0.1", "localhost"}
            or parsed_redirect.username is not None
            or parsed_redirect.password is not None
            or redirect_port is None
            or parsed_redirect.query
            or parsed_redirect.fragment
            or not parsed_redirect.path.startswith("/")
            or redirect_uri.count("*") > 1
            or ("*" in redirect_uri and ":*" not in redirect_uri)
        ):
            errors["TIKTOK_REDIRECT_URI"] = (
                "Use a loopback URI such as http://127.0.0.1:*/callback/."
            )

    if values["TELEGRAM_OWNER_USERNAME"] and not TELEGRAM_USERNAME.fullmatch(
        values["TELEGRAM_OWNER_USERNAME"]
    ):
        errors["TELEGRAM_OWNER_USERNAME"] = (
            "Use a Telegram username without @: letters, numbers, or underscores."
        )
    if values["INSTAGRAM_USERNAME"] and not USERNAME.fullmatch(values["INSTAGRAM_USERNAME"]):
        errors["INSTAGRAM_USERNAME"] = (
            "Use a username without @: letters, numbers, periods, or underscores."
        )

    if values["YOUTUBE_PRIVACY_STATUS"] not in {"private", "unlisted", "public"}:
        errors["YOUTUBE_PRIVACY_STATUS"] = "Choose private, unlisted, or public."
    if values["CAPTION_MODE"] != "message_or_empty":
        errors["CAPTION_MODE"] = "Only message_or_empty mode is supported."

    timezone = values["TIMEZONE"]
    if timezone:
        try:
            ZoneInfo(timezone)
        except (ValueError, ZoneInfoNotFoundError):
            errors["TIMEZONE"] = "Unknown IANA timezone."

    try:
        minimum_tags = int(values["AUTO_TAG_MIN"], 10)
        maximum_tags = int(values["AUTO_TAG_MAX"], 10)
    except ValueError:
        pass
    else:
        if minimum_tags > maximum_tags:
            errors["AUTO_TAG_MAX"] = "The maximum cannot be lower than the minimum."

    return errors
