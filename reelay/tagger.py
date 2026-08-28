import asyncio
import json
import random
import re
import shutil
from pathlib import Path


HASHTAG = re.compile(r"(?<!\w)#[\w]+", re.UNICODE)
CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\ud800-\udfff]")

BROAD_TAGS = (
    "#reels",
    "#viralreels",
    "#explorepage",
    "#trendingreels",
    "#instareels",
    "#reelitfeelit",
    "#viral",
    "#trending",
    "#fyp",
    "#reelsoftheday",
    "#viralvideos",
    "#reelsinstagram",
)

SAFE_FALLBACK = (
    "#reels",
    "#funnyreels",
    "#humor",
    "#memes",
    "#meme",
    "#comedy",
    "#relatable",
    "#viral",
    "#viralreels",
    "#trending",
    "#explorepage",
    "#instareels",
    "#reelitfeelit",
    "#fyp",
    "#dailyhumor",
    "#lol",
    "#entertainment",
    "#funnyvideos",
    "#reelsoftheday",
    "#goodvibes",
    "#viralvideos",
    "#reelsdaily",
)

CURATED_TITLES = {
    "work": "A Very Relatable Work Moment",
    "humor": "This Escalated Quickly",
    "relationship": "A Very Relatable Relationship Moment",
    "anime": "An Anime Moment Worth Rewatching",
    "travel": "A Moment From the Journey",
    "food": "That Taste Test Escalated Quickly",
    "fitness": "One More Rep",
    "pets": "This Pet Has So Much Personality",
    "beauty": "A Quick Beauty Moment",
    "fashion": "A Look Worth Saving",
    "cars": "A Car Moment Worth Rewatching",
    "tech": "A Quick Tech Moment",
    "gaming": "A Gaming Moment Worth Rewatching",
    "motivation": "Keep Going",
    "music": "A Moment for the Music",
}

GENERIC_TITLES = {
    "funny",
    "humor",
    "meme",
    "memes",
    "relatable",
    "reel",
    "reels",
    "trending",
    "viral",
    "video",
}

SOURCE_PROMO_TERMS = (
    "cast announced",
    "ending theme",
    "latest information",
    "new pv",
    "official latest",
    "opening theme",
    "pv公開",
    "オープニング主題歌",
    "エンディング主題歌",
    "キャスト",
    "公式最新情報",
    "最新情報",
)

SMALL_TITLE_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "but",
    "by",
    "for",
    "from",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "via",
    "with",
}

TOPICS = {
    "work": {
        "keywords": (
            "work",
            "working",
            "office",
            "coworker*",
            "colleague*",
            "corporate",
            "workplace",
            "job",
            "career",
            "employment",
            "meeting",
            "boss",
            "employee",
            "desk",
            "cubicle",
            "commute",
            "traffic",
            "subway",
            "monday",
            "работ*",
            "офис",
            "коллег*",
            "начальник",
            "метро",
            "пробка",
            "понедельник",
            "iş",
            "ofis",
        ),
        "tags": (
            "#workmood",
            "#mondayvibes",
            "#drivetowork",
            "#officelife",
            "#toxiccoworkers",
            "#relatablehumor",
            "#funnyreels",
            "#workstruggles",
            "#dailygrind",
            "#reelsoftheday",
            "#fyp",
            "#trending",
            "#workmemes",
            "#adulting",
            "#funnyvideos",
            "#morningcommute",
            "#stressedbutblessed",
        ),
    },
    "humor": {
        "keywords": (
            "humor",
            "funny",
            "comedy",
            "meme",
            "memes",
            "joke",
            "jokes",
            "lol",
            "lmao",
            "relatable",
            "pov",
            "when you",
            "me when",
            "nobody",
            "😂",
            "🤣",
            "юмор",
            "смешн*",
            "прикол",
            "мем",
            "шутка",
            "komik",
        ),
        "tags": (
            "#humor",
            "#funny",
            "#comedy",
            "#funnyreels",
            "#memes",
            "#meme",
            "#relatable",
            "#dailyhumor",
            "#lol",
            "#jokes",
            "#comedyreels",
        ),
    },
    "relationship": {
        "keywords": (
            "relationship*",
            "dating",
            "date night",
            "boyfriend",
            "girlfriend",
            "husband",
            "wife",
            "couple",
            "romance",
            "love",
            "wedding",
            "bride",
            "groom",
            "kiss",
            "heartbreak",
            "breakup",
            "ex boyfriend",
            "ex girlfriend",
            "отношен*",
            "любов*",
            "свидание",
            "расставан*",
            "ilişki",
            "aşk",
        ),
        "tags": (
            "#relationships",
            "#relationshipmemes",
            "#dating",
            "#love",
            "#couples",
            "#relationshipgoals",
            "#datinghumor",
            "#couplereels",
            "#relationshipadvice",
            "#relatable",
        ),
    },
    "anime": {
        "keywords": (
            "anime",
            "manga",
            "otaku",
            "cosplay",
            "cartoon",
            "animation",
            "animated",
            "comic book",
            "one piece",
            "onepiece",
            "tone piece",
            "luffy",
            "zoro",
            "naruto",
            "ワンピース",
            "エルバフ",
            "アニメ",
            "аниме",
            "манга",
        ),
        "tags": (
            "#anime",
            "#animereels",
            "#animeedit",
            "#animefans",
            "#otaku",
            "#manga",
            "#animecommunity",
            "#animeclips",
            "#animelover",
            "#animeworld",
            "#animememes",
        ),
    },
    "travel": {
        "keywords": (
            "travel*",
            "trip",
            "vacation",
            "holiday",
            "tourism",
            "tourist",
            "wanderlust",
            "adventure",
            "beach",
            "mountain",
            "landmark",
            "airport",
            "airplane",
            "hotel",
            "landscape",
            "cityscape",
            "путешеств*",
            "поездка",
            "отпуск",
            "туризм",
            "пляж",
            "seyahat",
            "tatil",
        ),
        "tags": (
            "#travel",
            "#travelreels",
            "#wanderlust",
            "#travelgram",
            "#exploremore",
            "#adventure",
            "#vacation",
            "#trip",
            "#travellife",
            "#beautifuldestinations",
        ),
    },
    "food": {
        "keywords": (
            "food",
            "foodie",
            "meal",
            "dish",
            "recipe",
            "cook*",
            "restaurant",
            "dessert",
            "cake",
            "pizza",
            "burger",
            "coffee",
            "kitchen",
            "еда",
            "рецепт",
            "готов*",
            "кухня",
            "ресторан",
            "yemek",
        ),
        "tags": (
            "#food",
            "#foodreels",
            "#foodie",
            "#foodlover",
            "#instafood",
            "#cooking",
            "#delicious",
            "#recipe",
            "#yummy",
            "#foodvideo",
        ),
    },
    "fitness": {
        "keywords": (
            "fitness",
            "workout",
            "gym",
            "exercise",
            "training",
            "weightlifting",
            "running",
            "runner",
            "yoga",
            "bodybuilding",
            "спорт",
            "фитнес",
            "трениров*",
            "зал",
            "spor",
        ),
        "tags": (
            "#fitness",
            "#fitnessreels",
            "#workout",
            "#gym",
            "#fitlife",
            "#training",
            "#healthylifestyle",
            "#gymmotivation",
            "#fitnessmotivation",
            "#workoutvideo",
        ),
    },
    "pets": {
        "keywords": (
            "pet",
            "pets",
            "animal",
            "dog",
            "dogs",
            "puppy",
            "cat",
            "cats",
            "kitten",
            "bird",
            "rabbit",
            "кот",
            "кошка",
            "собака",
            "щенок",
            "питомец",
            "kedi",
            "köpek",
        ),
        "tags": (
            "#pets",
            "#petreels",
            "#animals",
            "#petlover",
            "#cuteanimals",
            "#funnyanimals",
            "#petsofinstagram",
            "#petvideo",
            "#animalreels",
        ),
    },
    "beauty": {
        "keywords": (
            "beauty",
            "makeup",
            "cosmetic*",
            "skincare",
            "lipstick",
            "hairstyle",
            "haircare",
            "nail art",
            "nails",
            "макияж",
            "косметик*",
            "красота",
            "уход за кожей",
            "makyaj",
        ),
        "tags": (
            "#beauty",
            "#beautyreels",
            "#makeup",
            "#skincare",
            "#beautytips",
            "#makeuptutorial",
            "#glowup",
            "#beautylover",
            "#selfcare",
        ),
    },
    "fashion": {
        "keywords": (
            "fashion",
            "style",
            "outfit",
            "clothing",
            "streetwear",
            "dress",
            "shoes",
            "jewelry",
            "handbag",
            "model",
            "мода",
            "стиль",
            "одежда",
            "образ",
            "moda",
        ),
        "tags": (
            "#fashion",
            "#fashionreels",
            "#style",
            "#outfitinspo",
            "#ootd",
            "#streetstyle",
            "#fashionstyle",
            "#styleinspo",
            "#fashionlover",
        ),
    },
    "cars": {
        "keywords": (
            "car",
            "cars",
            "vehicle",
            "automobile",
            "automotive",
            "supercar",
            "motorcycle",
            "driving",
            "engine",
            "wheel",
            "машина",
            "авто",
            "автомобил*",
            "мотоцикл",
            "araba",
        ),
        "tags": (
            "#cars",
            "#carreels",
            "#carsofinstagram",
            "#automotive",
            "#carlover",
            "#carlifestyle",
            "#supercars",
            "#driving",
            "#carvideo",
        ),
    },
    "tech": {
        "keywords": (
            "technology",
            "tech",
            "gadget",
            "computer",
            "laptop",
            "smartphone",
            "software",
            "coding",
            "programming",
            "artificial intelligence",
            "robot",
            "технолог*",
            "гаджет",
            "ноутбук",
            "программирован*",
            "teknoloji",
        ),
        "tags": (
            "#tech",
            "#technology",
            "#techreels",
            "#gadgets",
            "#innovation",
            "#digital",
            "#technews",
            "#futuretech",
            "#techtips",
        ),
    },
    "gaming": {
        "keywords": (
            "gaming",
            "gamer",
            "video game",
            "gameplay",
            "game controller",
            "playstation",
            "xbox",
            "nintendo",
            "esports",
            "streamer",
            "minecraft",
            "fortnite",
            "гейминг",
            "игрок",
            "видеоигр*",
            "oyun",
        ),
        "tags": (
            "#gaming",
            "#gamer",
            "#gamingreels",
            "#gameplay",
            "#videogames",
            "#gamersofinstagram",
            "#gamingcommunity",
            "#instagaming",
            "#gamingclips",
        ),
    },
    "motivation": {
        "keywords": (
            "motivation",
            "motivational",
            "inspiration",
            "inspirational",
            "success",
            "discipline",
            "mindset",
            "goal",
            "goals",
            "never give up",
            "quote",
            "мотивац*",
            "успех",
            "дисциплин*",
            "цель",
            "motivasyon",
        ),
        "tags": (
            "#motivation",
            "#motivationalreels",
            "#inspiration",
            "#successmindset",
            "#discipline",
            "#mindset",
            "#goals",
            "#selfimprovement",
            "#nevergiveup",
        ),
    },
    "music": {
        "keywords": (
            "music",
            "song",
            "singer",
            "concert",
            "band",
            "musician",
            "guitar",
            "piano",
            "drums",
            "microphone",
            "dj",
            "dance",
            "opening theme",
            "ending theme",
            "музыка",
            "песня",
            "концерт",
            "танец",
            "müzik",
        ),
        "tags": (
            "#music",
            "#musicreels",
            "#musician",
            "#newmusic",
            "#musiclover",
            "#song",
            "#instamusic",
            "#musicvideo",
            "#nowplaying",
        ),
    },
}

SPECIAL_TAGS = (
    (
        "anime",
        ("one piece", "onepiece", "tone piece", "luffy", "zoro", "ワンピース", "エルバフ"),
        ("#onepiece", "#onepieceanime", "#luffy", "#onepiecefans"),
    ),
    (
        "relationship",
        ("heartbreak", "breakup", "расставан*"),
        ("#heartbreak", "#breakup", "#healing", "#movingon"),
    ),
    ("pets", ("dog", "dogs", "puppy", "собака", "щенок", "köpek"), ("#dogsofinstagram", "#dogreels")),
    ("pets", ("cat", "cats", "kitten", "кот", "кошка", "kedi"), ("#catsofinstagram", "#catreels")),
)


class AutoTagger:
    def __init__(self, settings):
        self.settings = settings
        self.enabled = getattr(settings, "auto_tags", True)
        self.min_tags = int(getattr(settings, "auto_tag_min", 15))
        self.max_tags = int(getattr(settings, "auto_tag_max", 20))
        if self.min_tags < 1 or self.min_tags > self.max_tags:
            raise RuntimeError("Invalid auto tag range")
        self.swift_source = Path(__file__).with_name("vision_tags.swift")
        self.binary = settings.data_dir / "bin" / "reelay-vision"
        self._compile_lock = asyncio.Lock()

    async def generate(self, video_path, context_text=""):
        if not self.enabled:
            return ""

        video_path = Path(video_path)
        source_tags, source_text = self._source_data(video_path.parent)
        tags = []
        self._extend_unique(tags, source_tags)
        self._extend_unique(tags, HASHTAG.findall(str(context_text or "")))
        if len(tags) >= self.max_tags:
            return self._render(tags)

        try:
            evidence = await self._vision_evidence(video_path)
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
            evidence = {"labels": [], "texts": []}

        ranked_topics, semantic_sources = self._rank_topics(
            str(context_text or ""), source_text, evidence
        )
        if ranked_topics:
            combined_text = "\n".join(text for text, _ in semantic_sources)
            top_topic = ranked_topics[0][0]
            for topic_name, keywords, special_tags in SPECIAL_TAGS:
                if topic_name == top_topic and any(
                    self._matches(combined_text, keyword)
                    for keyword in keywords
                ):
                    self._extend_unique(tags, special_tags)

            topic_names = [name for name, _ in ranked_topics[:3]]
            if topic_names[0] == "work":
                topic_names = ["work"]
            topic_sets = [TOPICS[name]["tags"] for name in topic_names]
            for index in range(max(map(len, topic_sets))):
                for topic_tags in topic_sets:
                    if index < len(topic_tags):
                        self._append_unique(tags, topic_tags[index])
                    if len(tags) >= self.max_tags:
                        break
                if len(tags) >= self.max_tags:
                    break

            self._extend_unique(tags, BROAD_TAGS)
        else:
            self._extend_unique(tags, SAFE_FALLBACK)

        self._extend_unique(tags, SAFE_FALLBACK)
        return self._render(tags)

    async def generate_title(self, video_path, context_text=""):
        video_path = Path(video_path)

        user_title = self._title_candidate(context_text, source=False)
        if user_title:
            return self._finish_title(user_title)

        try:
            evidence = await self._vision_evidence(video_path)
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
            evidence = {"labels": [], "texts": []}

        ocr_title = self._ocr_title(evidence)
        if ocr_title:
            return self._finish_title(
                self._english_title_case(ocr_title)
            )

        description, source_title = self._source_title_fields(
            video_path.parent
        )
        source_candidate = self._source_title_candidate(
            description, source_title
        )
        visual_topics, _ = self._rank_topics("", "", evidence)
        source_topics, _ = self._rank_topics(
            "",
            "\n".join((description, source_title)),
            {"labels": [], "texts": []},
        )
        topics_conflict = (
            visual_topics
            and source_topics
            and visual_topics[0][0] != source_topics[0][0]
        )
        if source_candidate and not topics_conflict:
            return self._finish_title(source_candidate)

        evidence_text = " ".join(
            [
                *(str(text) for text in evidence.get("texts", [])),
                *(
                    str(item.get("label") or "")
                    for item in evidence.get("labels", [])
                ),
            ]
        )
        if any(
            self._matches(evidence_text, value)
            for value in ("child", "baby", "kid")
        ):
            return "That Reaction Says Everything"

        if visual_topics:
            topic_names = [name for name, _ in visual_topics[:3]]
            if topic_names[0] == "pets":
                if any(
                    self._matches(evidence_text, value)
                    for value in ("cat", "kitten", "кошка", "кот", "kedi")
                ):
                    return "This Cat Is Not Impressed"
                if any(
                    self._matches(evidence_text, value)
                    for value in ("dog", "puppy", "собака", "щенок", "köpek")
                ):
                    return "This Dog Has So Much Personality"
            if "food" in topic_names and "humor" in topic_names:
                return "That Taste Test Escalated Quickly"
            curated = CURATED_TITLES.get(topic_names[0])
            if curated:
                return curated

        return "A Moment Worth Watching"

    def _source_title_fields(self, job_dir):
        info_files = sorted(job_dir.glob("*.info.json"))
        if not info_files:
            return "", ""
        try:
            metadata = json.loads(info_files[0].read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return "", ""
        return (
            str(metadata.get("description") or ""),
            str(metadata.get("title") or ""),
        )

    def _source_title_candidate(self, description, source_title):
        description_lines = []
        for line in str(description or "").splitlines():
            visible = HASHTAG.sub("", line)
            if any(character.isalnum() for character in visible):
                description_lines.append(line)

        if len(description_lines) <= 2:
            lines = description_lines
        else:
            lines = []
        for line in lines:
            if len(line.strip()) > 90:
                continue
            if any(
                term.casefold() in line.casefold()
                for term in SOURCE_PROMO_TERMS
            ):
                continue
            candidate = self._title_candidate(line, source=True)
            if candidate:
                return candidate

        if re.match(r"^\s*video\s+by\b", source_title, re.IGNORECASE):
            return ""
        return self._title_candidate(source_title, source=True)

    def _title_candidate(self, text, source):
        for raw_line in str(text or "").splitlines():
            line = HASHTAG.sub("", raw_line)
            line = re.sub(r"https?://\S+|www\.\S+", "", line)
            line = re.sub(r"(?<!\w)@[\w.]+", "", line)
            line = " ".join(line.split()).strip(" .,:;|_-–—")
            if not line:
                continue

            words = re.findall(r"[^\W_]+", line, re.UNICODE)
            letters = sum(character.isalnum() for character in line)
            if letters < 6:
                continue
            if line.casefold() in GENERIC_TITLES:
                continue
            if CJK.search(line):
                if not 6 <= letters <= 70:
                    continue
            elif len(words) < (3 if source else 2):
                continue
            if source and len(words) > 14:
                continue
            return line
        return ""

    def _ocr_title(self, evidence):
        lines = []
        for text in evidence.get("texts", []):
            candidate = self._title_candidate(text, source=False)
            if not candidate:
                continue
            words = re.findall(r"[^\W_]+", candidate, re.UNICODE)
            if not CJK.search(candidate) and not 3 <= len(words) <= 14:
                if not 2 <= len(words) <= 14:
                    continue
            lines.append(candidate)

        candidates = list(lines)
        for index in range(len(lines) - 1):
            combined = f"{lines[index]} {lines[index + 1]}"
            words = re.findall(r"[^\W_]+", combined, re.UNICODE)
            if len(words) <= 14 and len(combined) <= 100:
                candidates.append(combined)
        if not candidates:
            return ""
        return max(
            candidates,
            key=lambda value: (
                min(len(re.findall(r"[^\W_]+", value, re.UNICODE)), 12),
                len(value),
            ),
        )

    @staticmethod
    def _english_title_case(value):
        if not value.isascii():
            return value
        words = value.split()
        result = []
        for index, word in enumerate(words):
            plain = word.strip(".,:;!?()[]{}\"'").casefold()
            if index and plain in SMALL_TITLE_WORDS:
                result.append(word.lower())
            else:
                result.append(word[:1].upper() + word[1:].lower())
        return " ".join(result)

    @staticmethod
    def _finish_title(value):
        value = HASHTAG.sub("", str(value))
        value = re.sub(r"(?<!\w)@[\w.]+", "", value)
        value = " ".join(value.split()).strip(" .,:;|_-–—")
        if len(value) <= 70:
            return value

        sentence_end = max(
            value.rfind(mark, 0, 71) for mark in (".", "!", "?")
        )
        if sentence_end >= 35:
            return value[: sentence_end + 1].strip()
        word_end = value.rfind(" ", 0, 71)
        if word_end >= 35:
            return value[:word_end].rstrip(" .,:;|_-–—")
        return value[:100].rstrip(" .,:;|_-–—")

    def _render(self, tags):
        count = random.randint(self.min_tags, self.max_tags)
        return " ".join(tags[:count])

    def _source_data(self, job_dir):
        info_files = sorted(job_dir.glob("*.info.json"))
        if not info_files:
            return [], ""
        try:
            metadata = json.loads(info_files[0].read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return [], ""

        description = str(metadata.get("description") or "")
        title = str(metadata.get("title") or "")
        return HASHTAG.findall(description), "\n".join((description, title))

    def _rank_topics(self, context_text, source_text, evidence):
        ocr = "\n".join(
            str(text).strip()
            for text in evidence.get("texts", [])
            if str(text).strip()
        )
        labels = " ".join(
            str(item.get("label") or "")
            for item in evidence.get("labels", [])
            if float(item.get("confidence") or 0) >= 0.2
        )
        sources = (
            (context_text, 5),
            (source_text, 1),
            (ocr, 4),
            (labels, 1),
        )

        scores = []
        for order, (name, topic) in enumerate(TOPICS.items()):
            score = 0
            for text, weight in sources:
                matches = sum(
                    self._matches(text, keyword)
                    for keyword in topic["keywords"]
                )
                score += min(matches, 4) * weight
            if score:
                scores.append((name, score, order))

        scores.sort(key=lambda item: (-item[1], item[2]))
        return [(name, score) for name, score, _ in scores], sources

    @staticmethod
    def _matches(text, keyword):
        raw_text = str(text or "").casefold()
        raw_keyword = keyword.casefold()
        if raw_keyword in {"😂", "🤣"}:
            return raw_keyword in raw_text

        prefix = raw_keyword.endswith("*")
        if prefix:
            raw_keyword = raw_keyword[:-1]
        normalized_text = re.sub(r"[^\w]+", " ", raw_text).strip()
        normalized_keyword = re.sub(r"[^\w]+", " ", raw_keyword).strip()
        if not normalized_keyword:
            return False
        if CJK.search(normalized_keyword):
            return normalized_keyword in normalized_text
        if prefix:
            return any(
                word.startswith(normalized_keyword)
                for word in normalized_text.split()
            )
        if " " in normalized_keyword:
            return f" {normalized_keyword} " in f" {normalized_text} "
        return normalized_keyword in normalized_text.split()

    async def _vision_evidence(self, video_path):
        frames = await self._extract_frames(video_path)
        try:
            await self._ensure_binary()
            output = await self._run([str(self.binary), *map(str, frames)])
            return json.loads(output)
        finally:
            shutil.rmtree(video_path.parent / ".tag_frames", ignore_errors=True)

    async def _extract_frames(self, video_path):
        output = await self._run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ]
        )
        duration = float(output.strip())
        if duration <= 0:
            raise RuntimeError("Video duration is unavailable")

        frame_dir = video_path.parent / ".tag_frames"
        shutil.rmtree(frame_dir, ignore_errors=True)
        frame_dir.mkdir(parents=True)

        frames = []
        for index, fraction in enumerate((0.2, 0.5, 0.8), start=1):
            timestamp = min(max(duration * fraction, 0), max(duration - 0.05, 0))
            frame = frame_dir / f"frame-{index}.jpg"
            await self._run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-ss",
                    f"{timestamp:.3f}",
                    "-i",
                    str(video_path),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=720:-2",
                    "-q:v",
                    "3",
                    str(frame),
                ]
            )
            if frame.is_file():
                frames.append(frame)

        if not frames:
            raise RuntimeError("Could not extract frames for tags")
        return frames

    async def _ensure_binary(self):
        async with self._compile_lock:
            if (
                self.binary.is_file()
                and self.binary.stat().st_mtime >= self.swift_source.stat().st_mtime
            ):
                return
            self.binary.parent.mkdir(parents=True, exist_ok=True)
            await self._run(
                [
                    "xcrun",
                    "swiftc",
                    "-O",
                    str(self.swift_source),
                    "-framework",
                    "Vision",
                    "-framework",
                    "AppKit",
                    "-o",
                    str(self.binary),
                ]
            )

    @staticmethod
    def _append_unique(tags, tag):
        normalized = tag.casefold()
        if normalized not in {value.casefold() for value in tags}:
            tags.append(tag)

    def _extend_unique(self, tags, candidates, limit=None):
        limit = self.max_tags if limit is None else limit
        for tag in candidates:
            self._append_unique(tags, tag)
            if len(tags) >= limit:
                break

    async def _run(self, command):
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode:
            message = stderr.decode(errors="replace").strip()
            raise RuntimeError(message or f"Command failed: {process.returncode}")
        return stdout.decode(errors="replace")
