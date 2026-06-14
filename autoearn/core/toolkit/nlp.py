"""NLP and text analysis toolkit.

Pure Python — no ML libraries required. Optional NLTK is used where available
but every function has a full pure-Python fallback.

All public functions return str (JSON or plain text).
"""
from __future__ import annotations

import collections
import json
import math
import re
import statistics
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Stop words (English, 120+)
# ---------------------------------------------------------------------------

_STOPWORDS: frozenset[str] = frozenset(
    """
    a about above after again against all am an and any are aren't as at
    be because been before being below between both but by
    can't cannot could couldn't
    did didn't do does doesn't doing don't down during
    each few for from further
    get got had hadn't has hasn't have haven't having he he'd he'll he's
    her here here's hers herself him himself his how how's
    i i'd i'll i'm i've if in into is isn't it it's its itself
    just let's
    me more most mustn't my myself
    no nor not of off on once only or other ought our ours ourselves out over own
    same shan't she she'd she'll she's should shouldn't so some such
    than that that's the their theirs them themselves then there there's
    these they they'd they'll they're they've this those through to too
    under until up very was wasn't we we'd we'll we're we've were weren't
    what what's when when's where where's which while who who's whom why why's
    will with won't would wouldn't
    you you'd you'll you're you've your yours yourself yourselves
    """.split()
)

# ---------------------------------------------------------------------------
# Positive / negative word lists for sentiment (~100 words each)
# ---------------------------------------------------------------------------

_POS_WORDS: frozenset[str] = frozenset(
    """
    good great excellent amazing wonderful fantastic terrific superb outstanding
    brilliant awesome perfect love loved loving like liked enjoy enjoyed enjoying
    happy happiness joy joyful pleased pleasure delight delighted beautiful lovely
    best better impressive positive success successful win winning achieve accomplished
    thank thanks thankful grateful gratitude appreciate appreciated helpful kind
    generous recommend efficient effective easy nice cool fun exciting excited
    innovative smart clever benefit beneficial valuable fantastic flawless smooth
    seamless fast quick reliable trustworthy honest transparent clear wonderful
    phenomenal exceptional magnificent remarkable extraordinary splendid marvelous
    glorious stellar impressive top-notch first-rate high-quality commendable
    praiseworthy rewarding fulfilling satisfying refreshing uplifting inspiring
    creative bold empowering engaging
    """.split()
)

_NEG_WORDS: frozenset[str] = frozenset(
    """
    bad terrible awful horrible dreadful disgusting hate hated hating dislike
    angry anger furious frustrating frustrated annoying annoyed disappoint
    disappointed failure failed fail failing broken worst worse poor useless
    unreliable slow buggy painful problem issue error wrong misleading confusing
    difficult hard impossible mistake regret unhappy sad depressed missing crash
    fault defect defective inferior mediocre unacceptable unresponsive spam scam
    fraud fake lie rude unprofessional offensive harmful dangerous loss waste
    nasty appalling atrocious vile dreadful pathetic miserable deplorable
    disastrous catastrophic abysmal horrendous disgraceful shameful regrettable
    unpleasant troublesome inadequate substandard disappointing irritating
    annoying tedious exhausting overwhelming stressful problematic frustrating
    """.split()
)

_INTENSIFIERS: frozenset[str] = frozenset(
    "very extremely incredibly really absolutely positively utterly totally quite fairly somewhat".split()
)

_NEGATORS: frozenset[str] = frozenset(
    "not no never neither nor hardly barely scarcely don't doesn't didn't won't wouldn't can't cannot".split()
)

# ---------------------------------------------------------------------------
# Topic keyword banks
# ---------------------------------------------------------------------------

_TOPIC_KEYWORDS: dict[str, frozenset[str]] = {
    "technology": frozenset(
        "software hardware computer code programming python javascript ai machine learning "
        "cloud server database api app mobile internet network cyber security blockchain "
        "algorithm robot automation chip gpu cpu tech startup silicon".split()
    ),
    "finance": frozenset(
        "stock market investment portfolio dividend earnings revenue profit loss debt equity "
        "fund hedge bank interest rate inflation currency crypto bitcoin ethereum dollar "
        "treasury bond mortgage loan capital asset trading forex economy gdp".split()
    ),
    "health": frozenset(
        "health medical hospital doctor patient drug medicine treatment disease symptom "
        "nutrition diet exercise fitness weight calories vitamin mineral blood pressure "
        "diabetes cancer heart mental wellness therapy surgery clinical trial vaccine".split()
    ),
    "sports": frozenset(
        "game team player score goal win lose match tournament championship season league "
        "football soccer basketball baseball tennis golf cricket rugby swimming athletics "
        "olympic coach referee stadium pitch court athlete training injury".split()
    ),
    "politics": frozenset(
        "government president election vote law policy senate congress parliament minister "
        "democrat republican party campaign legislation bill court supreme justice military "
        "foreign policy trade diplomacy constitution amendment reform tax regulation".split()
    ),
    "entertainment": frozenset(
        "movie film actor actress director music song album artist concert festival award "
        "oscar grammy emmy celebrity star tv show series streaming netflix youtube tiktok "
        "instagram twitter celebrity media entertainment hollywood box office".split()
    ),
    "travel": frozenset(
        "travel trip vacation hotel flight airport destination country city tour adventure "
        "beach mountain resort cruise airline passport visa tourism guide luggage ticket "
        "booking accommodation itinerary backpacker sightseeing culture food local".split()
    ),
    "food": frozenset(
        "food recipe cooking chef restaurant meal dish cuisine ingredient flavor taste "
        "breakfast lunch dinner snack dessert vegetarian vegan organic healthy protein "
        "carb calorie kitchen bake fry roast grill coffee tea wine beer menu".split()
    ),
    "science": frozenset(
        "research study experiment data analysis result hypothesis theory evidence biology "
        "chemistry physics astronomy geology neuroscience genetics dna protein cell organism "
        "evolution climate environment nasa space satellite planet universe particle quantum".split()
    ),
    "business": frozenset(
        "business company startup enterprise product service customer market strategy growth "
        "management team employee hire sales marketing brand launch partner acquisition "
        "revenue profit quarter annual report ceo cfo founder investor venture capital".split()
    ),
}

# ---------------------------------------------------------------------------
# Spam trigger words
# ---------------------------------------------------------------------------

_SPAM_TRIGGERS: list[str] = [
    "free", "winner", "won", "prize", "cash", "urgent", "act now",
    "limited offer", "click here", "buy now", "order now", "congratulations",
    "guaranteed", "no risk", "100%", "make money", "earn money", "work from home",
    "income", "per day", "per week", "million dollars", "credit card", "password",
    "verify your account", "confirm your", "dear friend", "dear customer",
    "nigerian", "inheritance", "wire transfer", "lottery", "selected",
]

# ---------------------------------------------------------------------------
# Abbreviations to protect during sentence splitting
# ---------------------------------------------------------------------------

_ABBREVS = re.compile(
    r"\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|vs|etc|e\.g|i\.e|approx|dept|est|fig|"
    r"govt|incl|min|max|no|pp|rev|tel|st|ave|blvd|vol|jan|feb|mar|apr|"
    r"jun|jul|aug|sep|oct|nov|dec)\.",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_RE_WORD = re.compile(r"[a-zA-ZÀ-ɏ]+")
_RE_EMAIL = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_RE_URL = re.compile(r"https?://[^\s\"'<>]+|www\.[^\s\"'<>]+")
_RE_PHONE = re.compile(
    r"(?:\+?\d{1,3}[\s\-.])?(?:\(?\d{2,4}\)?[\s\-.]){1,3}\d{3,4}"
)
_RE_PRICE = re.compile(
    r"[$€£¥]\s?\d[\d,]*(?:\.\d{1,2})?|\d[\d,]*(?:\.\d{1,2})?\s?(?:USD|EUR|GBP|JPY|dollars?|euros?|pounds?)"
)
_RE_DATE = re.compile(
    r"""
    (?:\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})
    |(?:\d{4}[/\-\.]\d{1,2}[/\-\.]\d{1,2})
    |(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4})
    |(?:\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4})
    """,
    re.VERBOSE | re.IGNORECASE,
)
_RE_PROPER_NOUN = re.compile(r"(?<![.!?]\s)(?<!\n)\b([A-Z][a-z]{1,25})(?:\s+[A-Z][a-z]{1,25})+\b")
_RE_SYLLABLE = re.compile(r"[aeiouyAEIOUY]+")

# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

_LANG_SCRIPT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("zh", re.compile(r"[一-鿿]")),
    ("ja", re.compile(r"[぀-ヿ]")),
    ("ko", re.compile(r"[가-힯]")),
    ("ar", re.compile(r"[؀-ۿ]")),
    ("hi", re.compile(r"[ऀ-ॿ]")),
    ("ru", re.compile(r"[Ѐ-ӿ]")),
    ("el", re.compile(r"[Ͱ-Ͽ]")),
    ("he", re.compile(r"[֐-׿]")),
    ("th", re.compile(r"[฀-๿]")),
]

_LANG_FUNCTION_WORDS: dict[str, tuple[str, frozenset[str]]] = {
    "en": ("English", frozenset(["the", "and", "that", "for", "this", "with", "are", "was", "have", "not", "but", "from"])),
    "es": ("Spanish", frozenset(["que", "con", "del", "una", "los", "las", "por", "son", "para", "como", "pero", "los"])),
    "fr": ("French", frozenset(["les", "des", "une", "est", "que", "pas", "qui", "sur", "par", "avec", "dans", "plus"])),
    "de": ("German", frozenset(["die", "der", "und", "ist", "den", "ein", "eine", "ich", "sie", "das", "nicht", "mit"])),
    "it": ("Italian", frozenset(["che", "per", "non", "una", "sono", "del", "gli", "con", "nei", "tra", "del", "alla"])),
    "pt": ("Portuguese", frozenset(["que", "com", "uma", "por", "para", "não", "dos", "nas", "foi", "este", "mais", "ele"])),
    "nl": ("Dutch", frozenset(["van", "een", "het", "zijn", "door", "met", "dit", "bij", "ook", "maar", "voor", "niet"])),
}

# ---------------------------------------------------------------------------
# Internal utilities
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> list[str]:
    """Return lowercase alphabetic tokens."""
    return _RE_WORD.findall(text.lower())


def _tfidf_scores(
    tokens: list[str], stopwords: frozenset[str] | None = None
) -> dict[str, float]:
    """
    Single-document TF-IDF: tf * log(total / tf + 1).
    Acts as a proxy for term importance within one document.
    """
    if stopwords is None:
        stopwords = _STOPWORDS
    filtered = [t for t in tokens if t not in stopwords and len(t) > 1]
    if not filtered:
        return {}
    total = len(filtered)
    freq: dict[str, int] = collections.Counter(filtered)
    return {w: (cnt / total) * math.log(total / cnt + 1) for w, cnt in freq.items()}


def _count_syllables(word: str) -> int:
    """Approximate syllable count."""
    return max(1, len(_RE_SYLLABLE.findall(word.lower().rstrip("e"))))


# ---------------------------------------------------------------------------
# 1. summarize_text
# ---------------------------------------------------------------------------


def summarize_text(text: str, max_sentences: int = 5) -> str:
    """
    Extractive summarisation: score sentences by TF-IDF word weights.

    Returns JSON {summary, original_words, summary_words, compression_pct}.
    """
    sentences = json.loads(split_sentences(text))
    if not sentences:
        return json.dumps({"summary": "", "original_words": 0,
                           "summary_words": 0, "compression_pct": 0.0})

    all_tokens = _tokenize(text)
    original_words = len(all_tokens)
    tfidf = _tfidf_scores(all_tokens)

    scored: list[tuple[int, float, str]] = []
    for idx, sent in enumerate(sentences):
        words = _tokenize(sent)
        score = sum(tfidf.get(w, 0.0) for w in words) / max(len(words), 1)
        if idx < 3:
            score *= 1.2  # lead-bias
        scored.append((idx, score, sent))

    top = sorted(scored, key=lambda x: x[1], reverse=True)[:max_sentences]
    top_in_order = sorted(top, key=lambda x: x[0])
    summary = " ".join(s for _, _, s in top_in_order)
    summary_words = len(_tokenize(summary))
    compression = round((1 - summary_words / max(original_words, 1)) * 100, 1)

    return json.dumps({
        "summary": summary,
        "original_words": original_words,
        "summary_words": summary_words,
        "compression_pct": compression,
    }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 2. extract_entities
# ---------------------------------------------------------------------------


def extract_entities(text: str) -> str:
    """
    Regex-based NER: detect emails, URLs, phones, prices, dates, proper nouns.

    Returns JSON {emails, urls, phones, prices, dates, proper_nouns}.
    """
    emails = list(dict.fromkeys(_RE_EMAIL.findall(text)))
    urls = list(dict.fromkeys(_RE_URL.findall(text)))
    phones = list(dict.fromkeys(
        m.strip() for m in _RE_PHONE.findall(text)
        if len(re.sub(r"\D", "", m)) >= 7
    ))
    prices = list(dict.fromkeys(_RE_PRICE.findall(text)))
    dates = list(dict.fromkeys(_RE_DATE.findall(text)))
    proper_nouns = list(dict.fromkeys(_RE_PROPER_NOUN.findall(text)))

    return json.dumps({
        "emails": emails,
        "urls": urls,
        "phones": phones,
        "prices": prices,
        "dates": dates,
        "proper_nouns": proper_nouns,
    }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 3. sentiment_analysis
# ---------------------------------------------------------------------------


def sentiment_analysis(text: str) -> str:
    """
    Rule-based VADER-like sentiment scoring using embedded word lists.

    Returns JSON {sentiment, score, confidence, positive_words, negative_words}.
    """
    tokens = _tokenize(text)
    total = max(len(tokens), 1)
    pos_hits: list[str] = []
    neg_hits: list[str] = []
    score = 0.0
    intensifier_active = False

    for i, token in enumerate(tokens):
        multiplier = 1.5 if intensifier_active else 1.0
        intensifier_active = token in _INTENSIFIERS
        negated = any(tokens[j] in _NEGATORS for j in range(max(0, i - 3), i))

        if token in _POS_WORDS:
            delta = 1.0 * multiplier * (-1 if negated else 1)
            score += delta
            (neg_hits if negated else pos_hits).append(token)
        elif token in _NEG_WORDS:
            delta = -1.0 * multiplier * (-1 if negated else 1)
            score += delta
            (pos_hits if negated else neg_hits).append(token)

    normalised = score / (abs(score) + total / 5 + 1e-9)
    normalised = max(-1.0, min(1.0, normalised))

    if normalised >= 0.05:
        sentiment = "positive"
    elif normalised <= -0.05:
        sentiment = "negative"
    else:
        sentiment = "neutral"

    abs_score = abs(normalised)
    if abs_score >= 0.5:
        confidence = "high"
    elif abs_score >= 0.2:
        confidence = "medium"
    else:
        confidence = "low"

    return json.dumps({
        "sentiment": sentiment,
        "score": round(normalised, 4),
        "confidence": confidence,
        "positive_words": list(dict.fromkeys(pos_hits)),
        "negative_words": list(dict.fromkeys(neg_hits)),
    }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 4. detect_language
# ---------------------------------------------------------------------------


def detect_language(text: str) -> str:
    """
    Detect language by character set and common function words.

    Returns JSON {language, code, confidence}.
    """
    if not text.strip():
        return json.dumps({"language": "English", "code": "en", "confidence": 0.0})

    # Script detection (non-Latin)
    for code, pattern in _LANG_SCRIPT_PATTERNS:
        count = len(pattern.findall(text))
        if count > 3:
            names = {
                "zh": "Chinese", "ja": "Japanese", "ko": "Korean",
                "ar": "Arabic", "hi": "Hindi", "ru": "Russian",
                "el": "Greek", "he": "Hebrew", "th": "Thai",
            }
            confidence = min(1.0, count / 20)
            return json.dumps({
                "language": names.get(code, code),
                "code": code,
                "confidence": round(confidence, 3),
            })

    # Latin-script: score by common function word overlap
    token_set = set(_tokenize(text))
    best_code = "en"
    best_score = 0
    best_name = "English"
    for code, (name, words) in _LANG_FUNCTION_WORDS.items():
        overlap = len(token_set & words)
        if overlap > best_score:
            best_score = overlap
            best_code = code
            best_name = name

    total_tokens = max(len(token_set), 1)
    confidence = round(min(1.0, best_score / max(total_tokens * 0.15, 1)), 3)

    return json.dumps({
        "language": best_name,
        "code": best_code,
        "confidence": confidence,
    })


# ---------------------------------------------------------------------------
# 5. translate_text
# ---------------------------------------------------------------------------


def translate_text(text: str, target_lang: str, source_lang: str = "auto") -> str:
    """
    Translate text via LibreTranslate (free public endpoint) with MyMemory fallback.

    Returns translated text string (plain, not JSON) on success, or an error string.
    """
    if source_lang == "auto":
        detected = json.loads(detect_language(text))
        source_lang = detected.get("code", "en")

    # Try LibreTranslate first
    try:
        payload = {
            "q": text[:1000],
            "source": source_lang,
            "target": target_lang,
            "format": "text",
        }
        resp_lt = urllib.request.urlopen(
            urllib.request.Request(
                "https://libretranslate.com/translate",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json", "User-Agent": "autoearn/1.0"},
                method="POST",
            ),
            timeout=8,
        )
        lt_data = json.loads(resp_lt.read().decode())
        translated = lt_data.get("translatedText", "")
        if translated and translated != text:
            return translated
    except Exception:
        pass  # Fall through to MyMemory

    # MyMemory fallback
    try:
        lang_pair = f"{source_lang}|{target_lang}"
        params = urllib.parse.urlencode({"q": text[:500], "langpair": lang_pair})
        url = f"https://api.mymemory.translated.net/get?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "autoearn/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        translated = data.get("responseData", {}).get("translatedText", text)
        status = data.get("responseStatus", 200)
        if status != 200 or not translated:
            return text
        return translated
    except Exception as exc:
        return f"ERROR: translate_text: {exc}"


# ---------------------------------------------------------------------------
# 6. extract_keywords
# ---------------------------------------------------------------------------


def extract_keywords(text: str, num: int = 10) -> str:
    """
    Extract top keywords using single-document TF-IDF scoring.

    Returns JSON list of {word, score}.
    """
    tokens = _tokenize(text)
    scores = _tfidf_scores(tokens)
    top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:num]
    return json.dumps([{"word": w, "score": round(s, 6)} for w, s in top],
                      ensure_ascii=False)


# ---------------------------------------------------------------------------
# 7. text_similarity
# ---------------------------------------------------------------------------


def text_similarity(text1: str, text2: str) -> str:
    """
    Cosine similarity of TF-IDF vectors for two texts.

    Returns JSON {similarity, interpretation}.
    """
    scores1 = _tfidf_scores(_tokenize(text1))
    scores2 = _tfidf_scores(_tokenize(text2))

    vocab = set(scores1) | set(scores2)
    if not vocab:
        similarity = 0.0
    else:
        dot = sum(scores1.get(w, 0.0) * scores2.get(w, 0.0) for w in vocab)
        mag1 = math.sqrt(sum(v ** 2 for v in scores1.values()))
        mag2 = math.sqrt(sum(v ** 2 for v in scores2.values()))
        if mag1 == 0 or mag2 == 0:
            similarity = 0.0
        else:
            similarity = round(dot / (mag1 * mag2), 4)

    if similarity >= 0.95:
        interpretation = "identical"
    elif similarity >= 0.7:
        interpretation = "very_similar"
    elif similarity >= 0.4:
        interpretation = "similar"
    else:
        interpretation = "different"

    return json.dumps({"similarity": similarity, "interpretation": interpretation})


# ---------------------------------------------------------------------------
# 8. split_sentences
# ---------------------------------------------------------------------------


def split_sentences(text: str) -> str:
    """
    Split text into sentences using punctuation heuristics.

    Protects common abbreviations (Mr., Dr., etc.) from false splits.
    Returns JSON list of sentence strings.
    """
    text = re.sub(r"\s+", " ", text.strip())
    placeholder = _ABBREVS.sub(lambda m: m.group(0).replace(".", "<<<DOT>>>"), text)
    parts = re.split(r'(?<=[.!?])\s+(?=[A-Z"\'\(\[])', placeholder)
    sentences = [p.replace("<<<DOT>>>", ".").strip() for p in parts if p.strip()]
    return json.dumps(sentences, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 9. word_frequency
# ---------------------------------------------------------------------------


def word_frequency(text: str, top_n: int = 20) -> str:
    """
    Count word frequency, excluding stop words.

    Returns JSON list of {word, count, pct}.
    """
    tokens = [t for t in _tokenize(text) if t not in _STOPWORDS and len(t) > 1]
    total = max(len(tokens), 1)
    freq: dict[str, int] = collections.Counter(tokens)
    top = freq.most_common(top_n)
    result = [{"word": w, "count": c, "pct": round(c / total * 100, 2)} for w, c in top]
    return json.dumps(result, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 10. classify_intent
# ---------------------------------------------------------------------------


def classify_intent(text: str) -> str:
    """
    Classify the intent of a message into one of:
    question / command / statement / complaint / praise / request.

    Returns JSON {intent, confidence, signals}.
    """
    lower = text.strip().lower()
    tokens = set(_tokenize(lower))
    signals: list[str] = []

    scores: dict[str, float] = {
        "question": 0.0,
        "command": 0.0,
        "statement": 0.1,
        "complaint": 0.0,
        "praise": 0.0,
        "request": 0.0,
    }

    # Question
    if text.strip().endswith("?"):
        scores["question"] += 0.6
        signals.append("ends with ?")
    if re.match(r"^(what|who|where|when|why|how|is|are|was|were|do|does|did|can|could|would|should|may|might)\b", lower):
        scores["question"] += 0.4
        signals.append("question word opener")

    # Command
    cmd_verbs = frozenset(
        "do make tell show give send create write add remove delete update fix check "
        "verify list find run stop start open close help explain describe go".split()
    )
    first_word = lower.split()[0] if lower.split() else ""
    if first_word in cmd_verbs:
        scores["command"] += 0.5
        signals.append(f"command verb: {first_word}")

    # Request (polite command)
    if re.search(r"\b(please|could you|can you|would you|kindly)\b", lower):
        scores["request"] += 0.5
        signals.append("polite request phrase")

    # Complaint
    complaint_words = frozenset(
        "bad terrible broken doesn't wrong error issue problem frustrated annoying "
        "disappointed useless worst awful horrible".split()
    )
    if tokens & complaint_words:
        scores["complaint"] += 0.5
        signals.append("complaint keywords")
    if tokens & _NEGATORS and not (tokens & _NEGATORS) <= frozenset(["no", "not"]):
        scores["complaint"] += 0.15

    # Praise
    if tokens & _POS_WORDS:
        scores["praise"] += 0.4
        signals.append("positive words")
    if re.search(r"\b(thank|thanks|appreciate|great job|well done|love it)\b", lower):
        scores["praise"] += 0.3
        signals.append("praise phrase")

    best = max(scores, key=lambda k: scores[k])
    total = sum(scores.values()) or 1.0
    confidence = round(scores[best] / total, 3)

    return json.dumps({
        "intent": best,
        "confidence": confidence,
        "signals": signals,
    }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 11. extract_action_items
# ---------------------------------------------------------------------------

_ACTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?:TODO|FIXME|ACTION|TASK)[:\s]+(.{3,120})", re.IGNORECASE), "todo_tag"),
    (re.compile(r"(?:need(?:s)? to|must|should|have to|ought to)\s+\w.{3,100}", re.IGNORECASE), "modal_verb"),
    (re.compile(r"- \[ \]\s*(.{3,120})", re.MULTILINE), "checkbox"),
    (re.compile(r"(?:Action|Follow.?up|Next step|Deliverable)[:\s]+.{3,150}", re.IGNORECASE | re.MULTILINE), "action_header"),
    (re.compile(r"(?:please|pls)\s+(?:make sure|ensure|check|verify|update|fix|review|send|complete|finish|add|remove|create)\b.{0,120}", re.IGNORECASE), "please_verb"),
]

_HIGH_PRIORITY_WORDS = frozenset("urgent critical asap immediately must required mandatory deadline".split())
_LOW_PRIORITY_WORDS = frozenset("maybe consider could optional nice would when possible eventually".split())


def extract_action_items(text: str) -> str:
    """
    Find action items and TODOs in free-form text.

    Returns JSON list of {action, priority: high/medium/low}.
    """
    found: list[dict[str, str]] = []
    seen: set[str] = set()

    for pattern, tag in _ACTION_PATTERNS:
        for match in pattern.finditer(text):
            item = match.group(0).strip()
            key = re.sub(r"\s+", " ", item.lower())[:80]
            if key in seen or len(item) < 5:
                continue
            seen.add(key)
            words = frozenset(_tokenize(item))
            if words & _HIGH_PRIORITY_WORDS:
                priority = "high"
            elif words & _LOW_PRIORITY_WORDS:
                priority = "low"
            else:
                priority = "medium"
            found.append({"action": item, "priority": priority})

    return json.dumps(found, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 12. reading_grade_level
# ---------------------------------------------------------------------------


def reading_grade_level(text: str) -> str:
    """
    Flesch-Kincaid Grade Level and Gunning Fog index.

    Returns JSON {flesch_kincaid_grade, gunning_fog, interpretation}.
    """
    sentences_raw: list[str] = json.loads(split_sentences(text))
    num_sentences = max(len(sentences_raw), 1)
    tokens = [t for t in _tokenize(text) if t]
    num_words = max(len(tokens), 1)
    num_syllables = sum(_count_syllables(w) for w in tokens)

    # Flesch-Kincaid Grade Level
    asl = num_words / num_sentences
    asw = num_syllables / num_words
    fk_grade = round(0.39 * asl + 11.8 * asw - 15.59, 2)

    # Gunning Fog: 0.4 * (avg sentence length + percent complex words)
    complex_words = sum(1 for w in tokens if _count_syllables(w) >= 3)
    fog = round(0.4 * (asl + 100 * complex_words / num_words), 2)

    if fk_grade < 6:
        interpretation = "elementary"
    elif fk_grade < 9:
        interpretation = "middle school"
    elif fk_grade < 12:
        interpretation = "high school"
    elif fk_grade < 16:
        interpretation = "college"
    else:
        interpretation = "graduate"

    return json.dumps({
        "flesch_kincaid_grade": fk_grade,
        "gunning_fog": fog,
        "interpretation": interpretation,
        "words": num_words,
        "sentences": num_sentences,
        "syllables": num_syllables,
    }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 13. detect_spam
# ---------------------------------------------------------------------------


def detect_spam(text: str) -> str:
    """
    Rule-based spam detector.

    Returns JSON {score_0_to_1, is_spam, triggers}.
    """
    lower = text.lower()
    words = text.split()
    num_words = max(len(words), 1)
    triggers: list[str] = []
    score = 0.0

    # ALL-CAPS ratio
    caps_words = [w for w in words if len(w) >= 3 and w.isupper()]
    caps_ratio = len(caps_words) / num_words
    if caps_ratio > 0.15:
        score += min(0.3, caps_ratio)
        triggers.append(f"ALL_CAPS_ratio={caps_ratio:.0%}")

    # Exclamation marks
    excl_count = text.count("!")
    if excl_count > 2:
        score += min(0.2, excl_count * 0.05)
        triggers.append(f"excessive_exclamation_marks={excl_count}")

    # Spam trigger words/phrases
    trigger_hits: list[str] = []
    for trigger in _SPAM_TRIGGERS:
        if trigger in lower:
            trigger_hits.append(trigger)
    if trigger_hits:
        score += min(0.4, len(trigger_hits) * 0.08)
        triggers.append(f"spam_words={trigger_hits[:5]}")

    # URL count
    urls = _RE_URL.findall(text)
    if len(urls) > 3:
        score += min(0.2, len(urls) * 0.05)
        triggers.append(f"url_count={len(urls)}")

    # Punctuation density
    punct = sum(1 for c in text if c in "!?$%@#*")
    punct_density = punct / max(len(text), 1)
    if punct_density > 0.06:
        score += 0.1
        triggers.append(f"high_punct_density={punct_density:.0%}")

    score = round(min(score, 1.0), 4)
    return json.dumps({
        "score_0_to_1": score,
        "is_spam": score >= 0.5,
        "triggers": triggers,
    }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 14. anonymize_text
# ---------------------------------------------------------------------------

_RE_SSN = re.compile(r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b")
_RE_CARD = re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b")


def anonymize_text(text: str) -> str:
    """
    Replace PII in text with safe placeholders.

    Handles emails, phones, SSNs, credit cards, then proper-noun sequences.
    Returns the anonymised text as a plain string.
    """
    result = text

    # Emails
    result = _RE_EMAIL.sub("[EMAIL]", result)

    # URLs
    result = _RE_URL.sub("[URL]", result)

    # SSN patterns (before phone so digits aren't double-matched)
    result = _RE_SSN.sub("[SSN]", result)

    # Credit card numbers
    result = _RE_CARD.sub("[CARD]", result)

    # Phone numbers (7+ digits)
    def _redact_phone(m: re.Match[str]) -> str:
        raw = m.group(0).strip()
        digits = re.sub(r"\D", "", raw)
        return "[PHONE]" if len(digits) >= 7 else raw

    result = _RE_PHONE.sub(_redact_phone, result)

    # Prices
    result = _RE_PRICE.sub("[AMOUNT]", result)

    # Proper nouns (two+ consecutive title-case words after punctuation or mid-sentence)
    result = re.sub(
        r"(?<=[a-z,;:.!?]\s)([A-Z][a-z]{1,20}\s+){1,}[A-Z][a-z]{1,20}",
        "[NAME]",
        result,
    )

    return result


# ---------------------------------------------------------------------------
# 15. topic_classifier
# ---------------------------------------------------------------------------


def topic_classifier(text: str) -> str:
    """
    Classify text into a topic using keyword frequency matching.

    Topics: technology / finance / health / sports / politics /
            entertainment / travel / food / science / business.

    Returns JSON {topic, confidence, scores}.
    """
    tokens = set(_tokenize(text))
    scores: dict[str, float] = {}

    for topic, keywords in _TOPIC_KEYWORDS.items():
        overlap = tokens & keywords
        scores[topic] = len(overlap) / max(len(tokens), 1)

    best_topic = max(scores, key=lambda k: scores[k])
    total = sum(scores.values()) or 1.0
    confidence = round(scores[best_topic] / total, 3) if total > 0 else 0.0

    return json.dumps({
        "topic": best_topic,
        "confidence": confidence,
        "scores": {k: round(v, 5) for k, v in sorted(scores.items(), key=lambda x: -x[1])},
    }, ensure_ascii=False)
