import re
from copy import deepcopy
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
        help_text="Токен бота, который выдаёт BotFather.",
        placeholder="123456789:AA…",
        link="https://t.me/BotFather",
    ),
    _field(
        "TELEGRAM_OWNER_ID",
        "telegram",
        "Telegram owner ID",
        field_type="number",
        help_text="Необязательный numeric ID владельца. Бот может сохранить его после /start.",
        placeholder="123456789",
    ),
    _field(
        "TELEGRAM_OWNER_USERNAME",
        "telegram",
        "Telegram username",
        help_text="Username владельца без @, используемый до привязки numeric ID.",
        placeholder="pol4xer",
        default="pol4xer",
    ),
    _field(
        "META_APP_ID",
        "instagram_meta",
        "Meta App ID",
        help_text="ID приложения Meta, используемого для Instagram и Facebook Graph API.",
        placeholder="123456789012345",
        link="https://developers.facebook.com/apps/",
    ),
    _field(
        "META_APP_SECRET",
        "instagram_meta",
        "Meta App Secret",
        field_type="password",
        secret=True,
        help_text="Секрет приложения Meta. Оставьте пустым, чтобы сохранить текущее значение.",
        placeholder="••••••••••••••••",
        link="https://developers.facebook.com/apps/",
    ),
    _field(
        "META_IG_USER_ID",
        "instagram_meta",
        "Instagram professional account ID",
        required=True,
        help_text="Numeric ID профессионального Instagram-аккаунта в Meta Graph API.",
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
        help_text="Long-lived Page token для публикации в Instagram и Facebook.",
        placeholder="EAAB…",
        link="https://developers.facebook.com/tools/explorer/",
    ),
    _field(
        "META_API_VERSION",
        "instagram_meta",
        "Meta API version",
        required=True,
        help_text="Версия Graph API в формате v26.0.",
        placeholder="v26.0",
        default="v26.0",
        link="https://developers.facebook.com/docs/graph-api/changelog/",
    ),
    _field(
        "INSTAGRAM_USERNAME",
        "instagram_meta",
        "Instagram username",
        required=True,
        help_text="Username целевого Instagram-аккаунта без @.",
        placeholder="rbc_haze_harris",
        link="https://www.instagram.com/accounts/edit/",
    ),
    _field(
        "CHROME_PROFILE",
        "instagram_meta",
        "Chrome profile",
        help_text="Профиль Chrome для разрешённых приватных источников.",
        placeholder="Default",
        default="Default",
    ),
    _field(
        "ALLOW_PRIVATE_SOURCES",
        "instagram_meta",
        "Allow private sources",
        field_type="toggle",
        help_text="Разрешить downloader читать cookies выбранного профиля Chrome.",
        default="false",
    ),
    _field(
        "PUBLISH_FACEBOOK",
        "facebook",
        "Publish to Facebook",
        field_type="toggle",
        required=True,
        help_text="Добавлять Facebook Page Reels в общий publish pipeline.",
        default="false",
        link="https://developers.facebook.com/docs/video-api/guides/reels-publishing/",
    ),
    _field(
        "META_PAGE_ID",
        "facebook",
        "Facebook Page ID",
        help_text="Numeric Page ID. Обязателен, когда публикация в Facebook включена.",
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
        help_text="Токен пользователя для получения long-lived Page token; runtime его не использует.",
        placeholder="EAAB…",
        link="https://developers.facebook.com/tools/explorer/",
    ),
    _field(
        "PUBLISH_THREADS",
        "threads",
        "Publish to Threads",
        field_type="toggle",
        required=True,
        help_text="Добавлять Threads video posts в общий publish pipeline.",
        default="false",
        link="https://developers.facebook.com/docs/threads/",
    ),
    _field(
        "THREADS_API_VERSION",
        "threads",
        "Threads API version",
        required=True,
        help_text="Версия Threads API в формате v1.0.",
        placeholder="v1.0",
        default="v1.0",
        link="https://developers.facebook.com/docs/threads/changelog/",
    ),
    _field(
        "THREADS_APP_ID",
        "threads",
        "Threads App ID",
        help_text="ID приложения с use case Threads API.",
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
        help_text="Секрет Threads-приложения. Оставьте пустым, чтобы сохранить текущий.",
        placeholder="••••••••••••••••",
        depends_on="PUBLISH_THREADS",
        link="https://developers.facebook.com/apps/",
    ),
    _field(
        "THREADS_USER_ID",
        "threads",
        "Threads User ID",
        help_text="Numeric Threads User ID. Обязателен при включённой публикации.",
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
        help_text="Long-lived token со scopes threads_basic и threads_content_publish.",
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
        help_text="Добавлять YouTube Shorts в общий publish pipeline.",
        default="false",
        link="https://console.cloud.google.com/apis/library/youtube.googleapis.com",
    ),
    _field(
        "YOUTUBE_CLIENT_ID",
        "youtube",
        "OAuth client ID",
        help_text="Client ID OAuth Desktop app из Google Cloud Console.",
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
        help_text="Client secret OAuth Desktop app. Пустое поле сохраняет текущий секрет.",
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
        help_text="Refresh token после запуска YouTube OAuth bootstrap.",
        placeholder="1//…",
        depends_on="PUBLISH_YOUTUBE",
        link="https://developers.google.com/youtube/v3/guides/auth/installed-apps",
    ),
    _field(
        "YOUTUBE_CHANNEL_ID",
        "youtube",
        "YouTube channel ID",
        help_text="ID подтверждённого OAuth-канала, обычно начинается с UC.",
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
        help_text="Privacy status новых YouTube uploads.",
        options=(
            {"value": "private", "label": "Private"},
            {"value": "unlisted", "label": "Unlisted"},
            {"value": "public", "label": "Public"},
        ),
        default="private",
        link="https://support.google.com/youtube/contact/yt_api_form",
    ),
    _field(
        "TIMEZONE",
        "schedule_storage",
        "Timezone",
        required=True,
        help_text="IANA timezone, по которой рассчитывается расписание.",
        placeholder="Europe/Istanbul",
        default="Europe/Istanbul",
        link="https://en.wikipedia.org/wiki/List_of_tz_database_time_zones",
    ),
    _field(
        "POSTS_PER_DAY",
        "schedule_storage",
        "Posts per day",
        field_type="number",
        required=True,
        help_text="От 1 до 12 равномерных слотов в окне публикации.",
        placeholder="5",
        default="5",
    ),
    _field(
        "POST_TIMES",
        "schedule_storage",
        "Exact publish times",
        help_text=(
            "Необязательный список точных слотов через запятую. "
            "Если заполнен, имеет приоритет над количеством и окном публикации."
        ),
        placeholder="13:00,18:30,21:30",
    ),
    _field(
        "POST_WINDOW_START",
        "schedule_storage",
        "Publish window starts",
        field_type="time",
        required=True,
        help_text="Начало окна публикации в формате HH:MM.",
        placeholder="09:00",
        default="09:00",
    ),
    _field(
        "POST_WINDOW_END",
        "schedule_storage",
        "Publish window ends",
        field_type="time",
        required=True,
        help_text="Конец окна публикации; ночное окно через полночь поддерживается.",
        placeholder="21:00",
        default="21:00",
    ),
    _field(
        "SCHEDULE_GRACE_MINUTES",
        "schedule_storage",
        "Catch-up grace, minutes",
        field_type="number",
        required=True,
        help_text="Как долго после пробуждения можно догнать один пропущенный слот.",
        placeholder="30",
        default="30",
    ),
    _field(
        "POST_ON_WEEKENDS",
        "schedule_storage",
        "Post on weekends",
        field_type="toggle",
        required=True,
        help_text="Разрешить расписанию работать в субботу и воскресенье.",
        default="true",
    ),
    _field(
        "CAPTION_MODE",
        "schedule_storage",
        "Caption mode",
        field_type="select",
        required=True,
        help_text="Режим формирования caption для элемента очереди.",
        options=({"value": "message_or_empty", "label": "Message or empty"},),
        default="message_or_empty",
    ),
    _field(
        "SEND_MP4_AUTOMATICALLY",
        "schedule_storage",
        "Send MP4 automatically",
        field_type="toggle",
        required=True,
        help_text="Автоматически отправлять владельцу скачанный MP4 в Telegram.",
        default="false",
    ),
    _field(
        "DELETE_AFTER_PUBLISH",
        "schedule_storage",
        "Delete after publishing",
        field_type="toggle",
        required=True,
        help_text="Удалять локальный MP4 после успешной публикации во все платформы.",
        default="true",
    ),
    _field(
        "AUTO_TAGS",
        "schedule_storage",
        "Generate hashtags",
        field_type="toggle",
        required=True,
        help_text="Автоматически определять тему ролика и генерировать hashtags.",
        default="true",
    ),
    _field(
        "AUTO_TAG_MIN",
        "schedule_storage",
        "Minimum hashtags",
        field_type="number",
        required=True,
        help_text="Минимум hashtags, от 0 до 30 и не больше максимума.",
        placeholder="15",
        default="15",
    ),
    _field(
        "AUTO_TAG_MAX",
        "schedule_storage",
        "Maximum hashtags",
        field_type="number",
        required=True,
        help_text="Максимум hashtags, от 0 до 30 и не меньше минимума.",
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


def public_schema():
    return deepcopy(CONFIG_SCHEMA)


def effective_values(stored):
    return {key: str(stored.get(key, DEFAULTS[key]) or "") for key in ALLOWED_KEYS}


def normalize_updates(values):
    if not isinstance(values, dict):
        return {}, {"values": "Ожидается объект с изменёнными настройками."}

    normalized = {}
    errors = {}
    for key, value in values.items():
        if key not in ALLOWED_KEYS:
            errors[key] = "Неизвестная настройка."
            continue
        if key in BOOLEAN_KEYS:
            if isinstance(value, bool):
                normalized[key] = "true" if value else "false"
                continue
            if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
                normalized[key] = value.strip().lower()
                continue
            errors[key] = "Укажите true или false."
            continue
        if key in INTEGER_RANGES:
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                errors[key] = "Укажите целое число."
                continue
            raw_value = str(value).strip()
            if key == "TELEGRAM_OWNER_ID" and not raw_value:
                normalized[key] = ""
                continue
            try:
                normalized[key] = str(int(raw_value, 10))
            except ValueError:
                errors[key] = "Укажите целое число."
            continue
        if key == "POST_TIMES":
            if not isinstance(value, str):
                errors[key] = "Укажите времена текстом через запятую."
                continue
            try:
                normalized[key] = ",".join(parse_post_times(value.strip()))
            except ValueError:
                errors[key] = (
                    "Укажите 1–12 уникальных времён HH:MM через запятую строго по возрастанию."
                )
            continue
        if not isinstance(value, str):
            errors[key] = "Укажите текстовое значение."
            continue
        value = value.strip()
        if len(value) > 16_384:
            errors[key] = "Значение слишком длинное."
            continue
        if any(character in value for character in ("\x00", "\r", "\n")):
            errors[key] = "Переносы строк и NUL недопустимы."
            continue
        if "${" in value:
            errors[key] = "Последовательность ${…} несовместима с форматом .env."
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
            errors[key] = "Обязательное поле."

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
    }
    for flag, keys in conditional.items():
        if values[flag] == "true":
            for key in keys:
                if not values[key]:
                    errors[key] = f"Обязательное поле, когда {flag}=true."

    for key in BOOLEAN_KEYS:
        if values[key] not in {"true", "false"}:
            errors[key] = "Допустимы только true или false."

    for key, (minimum, maximum) in INTEGER_RANGES.items():
        value = values[key]
        if key == "TELEGRAM_OWNER_ID" and not value:
            continue
        try:
            number = int(value, 10)
        except ValueError:
            errors[key] = "Укажите целое число."
            continue
        if not minimum <= number <= maximum:
            errors[key] = f"Допустимый диапазон: {minimum}–{maximum}."

    for key in ("POST_WINDOW_START", "POST_WINDOW_END"):
        if values[key] and not CLOCK_TIME.fullmatch(values[key]):
            errors[key] = "Используйте формат HH:MM."
    if values["POST_TIMES"]:
        try:
            parse_post_times(values["POST_TIMES"])
        except ValueError:
            errors["POST_TIMES"] = (
                "Укажите 1–12 уникальных времён HH:MM через запятую строго по возрастанию."
            )
    if (
        not values["POST_TIMES"]
        and values["POSTS_PER_DAY"] != "1"
        and values["POST_WINDOW_START"]
        and values["POST_WINDOW_START"] == values["POST_WINDOW_END"]
    ):
        errors["POST_WINDOW_END"] = "Для нескольких публикаций окно не может быть нулевым."

    for key in ("META_API_VERSION", "THREADS_API_VERSION"):
        if values[key] and not API_VERSION.fullmatch(values[key]):
            errors[key] = "Используйте формат версии вроде v26.0."

    for key in ("TELEGRAM_OWNER_USERNAME", "INSTAGRAM_USERNAME"):
        if values[key] and not USERNAME.fullmatch(values[key]):
            errors[key] = "Используйте username без @: буквы, цифры, точка или underscore."

    if values["YOUTUBE_PRIVACY_STATUS"] not in {"private", "unlisted", "public"}:
        errors["YOUTUBE_PRIVACY_STATUS"] = "Выберите private, unlisted или public."
    if values["CAPTION_MODE"] != "message_or_empty":
        errors["CAPTION_MODE"] = "Поддерживается только режим message_or_empty."

    timezone = values["TIMEZONE"]
    if timezone:
        try:
            ZoneInfo(timezone)
        except (ValueError, ZoneInfoNotFoundError):
            errors["TIMEZONE"] = "Неизвестная IANA timezone."

    try:
        minimum_tags = int(values["AUTO_TAG_MIN"], 10)
        maximum_tags = int(values["AUTO_TAG_MAX"], 10)
    except ValueError:
        pass
    else:
        if minimum_tags > maximum_tags:
            errors["AUTO_TAG_MAX"] = "Максимум не может быть меньше минимума."

    return errors
