"""Machine translation of a subtitle document (the `translate` operation).

Engine choice, licences and sources: docs/adr/ADR-004-translation-engine.md.

Three engines sit behind one interface (`TranslationEngine.translate`): the
offline default `argos` (Argos Translate, an optional import, never asked to
download anything), the optional external `deepl` (key only from the
environment), and the built-in `memory` (exact source -> target pairs the
caller supplies; deterministic, no model, no network). Whatever the engine,
the same mechanical steps run around it:

1. each cue's lines are joined into one segment, so the engine sees the
   sentence rather than half of it;
2. glossary terms are passed as a native glossary (DeepL) or substituted into
   the source (Argos); `memory` is looked up by the original text;
3. the translated text is re-broken for the *target* language (locale
   profiles' full-width counting and kinsoku, or word wrap at spaces);
4. ids, start/end times, speaker and style are copied unchanged -- timing is
   never stretched, merged or split;
5. each glossary term is checked in the output (`GLOSSARY_TERM_MISSING`).

Validation of the translated document (line length, reading speed, ...) is
the caller's (`operations._run_translate`) job, with the target language's
locale profile. Nothing here rewrites a translation to make it fit.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from typing import Any, Callable, List, Mapping, Optional, Sequence, Tuple

from .errors import SubtitleSkillError
from .locales import (
    LocaleProfile,
    _join_lines,
    _legal_split,
    _render,
    _SPACES,
    _tokenize,
    _width,
    break_lines,
    profile_for,
)
from .models import SubtitleCue, SubtitleDocument
from .provenance import canonical_json

#: Request `engine` values. `argos` is the default (ADR-004 decision 2).
ENGINES = ("argos", "deepl", "memory")
DEFAULT_ENGINE = "argos"

#: Read only from the environment -- a request may never carry a key
#: (`security.FORBIDDEN_KEYS` refuses `api_key` anywhere in a request).
DEEPL_KEY_ENV = "DEEPL_AUTH_KEY"
DEEPL_URL_ENV = "SUBTITLE_SKILL_DEEPL_API_URL"
DEEPL_FREE_URL = "https://api-free.deepl.com"
DEEPL_PRO_URL = "https://api.deepl.com"
#: DeepL limits a request to 128 KiB (API reference, POST /v2/translate);
#: texts are sent in batches well under that.
_DEEPL_BATCH_BYTES = 64 * 1024

GLOSSARY_LIMIT = 1000
TEXT_LIMIT = 2000


@dataclass(frozen=True)
class GlossaryEntry:
    source: str
    target: str


def parse_glossary(raw: Any) -> Tuple[GlossaryEntry, ...]:
    """`[{"source": "...", "target": "..."}]` -> entries, longest source first."""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise SubtitleSkillError("INVALID_INPUT", "glossary must be an array of {source, target} objects")
    if len(raw) > GLOSSARY_LIMIT:
        raise SubtitleSkillError("INVALID_INPUT", f"glossary has more than {GLOSSARY_LIMIT} entries")
    entries = []
    seen = {}
    for i, item in enumerate(raw):
        if not isinstance(item, Mapping) or set(item) != {"source", "target"}:
            raise SubtitleSkillError("INVALID_INPUT", f"glossary[{i}] must be an object with exactly 'source' and 'target'")
        src, tgt = item["source"], item["target"]
        for name, value in (("source", src), ("target", tgt)):
            if not isinstance(value, str) or not value.strip() or "\n" in value or "\t" in value:
                raise SubtitleSkillError(
                    "INVALID_INPUT", f"glossary[{i}].{name} must be a non-empty single-line string without tabs"
                )
        if src in seen and seen[src] != tgt:
            raise SubtitleSkillError("INVALID_INPUT", f"glossary source term {src!r} is given two targets")
        if src not in seen:
            seen[src] = tgt
            entries.append(GlossaryEntry(src, tgt))
    return tuple(sorted(entries, key=lambda e: (-len(e.source), e.source)))


def parse_memory(raw: Any) -> dict:
    """`[{"source": "...", "target": "..."}]` -> {source: target}."""
    if raw is None:
        return {}
    if not isinstance(raw, list):
        raise SubtitleSkillError("INVALID_INPUT", "translation_memory must be an array of {source, target} objects")
    out: dict = {}
    for i, item in enumerate(raw):
        if not isinstance(item, Mapping) or set(item) != {"source", "target"}:
            raise SubtitleSkillError(
                "INVALID_INPUT", f"translation_memory[{i}] must be an object with exactly 'source' and 'target'"
            )
        src, tgt = item["source"], item["target"]
        if not isinstance(src, str) or not isinstance(tgt, str) or not src.strip() or not tgt.strip():
            raise SubtitleSkillError("INVALID_INPUT", f"translation_memory[{i}] source/target must be non-empty strings")
        key = _normalize_segment(src)
        if key in out and out[key] != tgt:
            raise SubtitleSkillError("INVALID_INPUT", f"translation_memory gives two targets for {src!r}")
        out[key] = tgt
    return out


def _normalize_segment(text: str) -> str:
    return " ".join(text.split())


def sha256_json(data: Any) -> str:
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Engines
# --------------------------------------------------------------------------


class TranslationEngine:
    """One engine. `translate` returns exactly one string per input text."""

    name = ""

    def version(self) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def available(self) -> Tuple[bool, Optional[str]]:  # pragma: no cover - overridden
        raise NotImplementedError

    def native_glossary(self) -> bool:
        return False

    def translate(
        self, texts: Sequence[str], source: str, target: str, glossary: Sequence[GlossaryEntry]
    ) -> List[str]:  # pragma: no cover - overridden
        raise NotImplementedError


class MemoryEngine(TranslationEngine):
    """Exact lookup in a caller-supplied translation memory (ADR-004)."""

    name = "memory"

    def __init__(self, memory: Mapping[str, str]):
        self.memory = dict(memory)
        # a ja/zh line break carries no space, so a segment joined from two
        # lines may differ from the memory's source only in whitespace
        self._compact = {}
        for key, value in self.memory.items():
            self._compact.setdefault("".join(key.split()), value)

    def _lookup(self, text: str) -> Optional[str]:
        key = _normalize_segment(text)
        if key in self.memory:
            return self.memory[key]
        return self._compact.get("".join(key.split()))

    def version(self) -> str:
        return "memory:" + sha256_json(sorted(self.memory.items()))[:16]

    def available(self) -> Tuple[bool, Optional[str]]:
        return True, None

    def translate(self, texts, source, target, glossary):
        out = []
        missing = []
        for text in texts:
            found = self._lookup(text)
            if found is None:
                missing.append(text)
            else:
                out.append(found)
        if missing:
            raise SubtitleSkillError(
                "MISSING_INPUT",
                f"translation_memory has no entry for {len(missing)} segment(s), e.g. {missing[0]!r}",
                details={"missing_segments": missing},
            )
        return out


def _argos_code(language: str) -> str:
    return language.split("-", 1)[0].lower()


class ArgosEngine(TranslationEngine):
    """Argos Translate, offline only (ADR-004 decision 2).

    Never calls a download API (`update_package_index`,
    `install_package_for_language_pair`, ...): the language package must
    already be installed by the user. Refused unless Argos is set to its
    local OpenNMT provider -- Argos can otherwise forward to LibreTranslate
    or OpenAI, which would make `argos` not mean offline.
    """

    name = "argos"

    def _modules(self):
        try:
            import argostranslate.package as package  # type: ignore
            import argostranslate.translate as translate  # type: ignore
        except ImportError as exc:
            raise SubtitleSkillError(
                "DEPENDENCY_ERROR",
                "engine 'argos' needs Argos Translate (`pip install argostranslate`) and an installed "
                "language package for the pair; subtitle-skill never downloads one (docs/adr/ADR-004)",
            ) from exc
        try:
            import argostranslate.settings as settings  # type: ignore
        except ImportError:  # pragma: no cover - every released argostranslate has settings
            settings = None
        return package, translate, settings

    def _check_local_provider(self, settings) -> None:
        if settings is None:
            return
        provider = getattr(settings, "model_provider", None)
        enum = getattr(settings, "ModelProvider", None)
        local = getattr(enum, "OPENNMT", None) if enum is not None else None
        if provider is not None and local is not None and provider != local:
            raise SubtitleSkillError(
                "DEPENDENCY_ERROR",
                f"Argos Translate is configured for a remote model provider ({provider}); "
                "subtitle-skill's 'argos' engine is offline only -- set it back to the local OpenNMT provider",
            )

    def _package(self, package_module, source: str, target: str):
        src, tgt = _argos_code(source), _argos_code(target)
        for pkg in package_module.get_installed_packages():
            if getattr(pkg, "from_code", None) == src and getattr(pkg, "to_code", None) == tgt:
                return pkg
        raise SubtitleSkillError(
            "DEPENDENCY_ERROR",
            f"no installed Argos Translate package for {src}->{tgt}; install one yourself "
            f"(e.g. `argospm install translate-{src}_{tgt}`) -- subtitle-skill never downloads models",
        )

    def version(self) -> str:
        try:
            from importlib.metadata import version

            return "argostranslate " + version("argostranslate")
        except Exception:  # noqa: BLE001 - metadata is informational only
            return "argostranslate unknown"

    def pair_version(self, source: str, target: str) -> str:
        package_module, _translate, _settings = self._modules()
        pkg = self._package(package_module, source, target)
        return f"{self.version()}; package {_argos_code(source)}_{_argos_code(target)} {getattr(pkg, 'package_version', '?')}"

    def available(self) -> Tuple[bool, Optional[str]]:
        try:
            self._modules()
        except SubtitleSkillError as exc:
            return False, exc.message
        return True, None

    def translate(self, texts, source, target, glossary):
        package_module, translate_module, settings = self._modules()
        self._check_local_provider(settings)
        self._package(package_module, source, target)
        translation = translate_module.get_translation_from_codes(_argos_code(source), _argos_code(target))
        if translation is None:
            raise SubtitleSkillError(
                "DEPENDENCY_ERROR", f"Argos Translate has no translation for {source}->{target}"
            )
        out = []
        for text in texts:
            result = translation.translate(text)
            if not isinstance(result, str):
                raise SubtitleSkillError("TOOL_ERROR", "Argos Translate returned a non-string translation")
            out.append(result)
        return out


#: (method, url, headers, body bytes or None) -> (status, body bytes). Replaced in tests.
HttpFunc = Callable[[str, str, Mapping[str, str], Optional[bytes]], Tuple[int, bytes]]


def _urllib_http(method: str, url: str, headers: Mapping[str, str], body: Optional[bytes]) -> Tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers=dict(headers))
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - fixed https base URL
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, OSError) as exc:
        raise SubtitleSkillError("DEPENDENCY_ERROR", f"DeepL API unreachable: {exc}") from exc


def deepl_target_code(language: str) -> str:
    """BCP 47 -> DeepL target_lang. English and Portuguese need a region
    (ADR-004 decision 3); a bare `en` is sent as EN-US."""
    parts = language.split("-")
    primary = parts[0].upper()
    region = parts[1].upper() if len(parts) > 1 else ""
    if primary == "EN":
        return "EN-GB" if region == "GB" else "EN-US"
    if primary == "PT":
        return "PT-PT" if region == "PT" else "PT-BR"
    if primary == "ZH":
        if region in ("TW", "HK", "MO") or (len(parts) > 1 and parts[1].lower() == "hant"):
            return "ZH-HANT"
        return "ZH-HANS"
    return primary


class DeepLEngine(TranslationEngine):
    """DeepL API v2 (`/v2/translate`, `/v2/glossaries`), optional."""

    name = "deepl"
    API_VERSION = "v2"

    def __init__(self, http: Optional[HttpFunc] = None, environ: Optional[Mapping[str, str]] = None):
        self.http = http or _urllib_http
        self.environ = os.environ if environ is None else environ

    def _key(self) -> str:
        key = self.environ.get(DEEPL_KEY_ENV, "").strip()
        if not key:
            raise SubtitleSkillError(
                "DEPENDENCY_ERROR", f"engine 'deepl' needs an API key in the {DEEPL_KEY_ENV} environment variable"
            )
        return key

    def base_url(self) -> str:
        override = self.environ.get(DEEPL_URL_ENV, "").strip()
        if override:
            if not override.startswith("https://"):
                raise SubtitleSkillError("DEPENDENCY_ERROR", f"{DEEPL_URL_ENV} must be an https:// URL")
            return override.rstrip("/")
        return DEEPL_FREE_URL if self._key().endswith(":fx") else DEEPL_PRO_URL

    def version(self) -> str:
        return f"deepl-api {self.API_VERSION}"

    def available(self) -> Tuple[bool, Optional[str]]:
        try:
            self._key()
        except SubtitleSkillError as exc:
            return False, exc.message
        return True, None

    def native_glossary(self) -> bool:
        return True

    def _call(self, method: str, path: str, payload: Optional[dict]) -> dict:
        headers = {"Authorization": f"DeepL-Auth-Key {self._key()}", "User-Agent": "subtitle-skill"}
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        status, raw = self.http(method, self.base_url() + path, headers, body)
        if status in (401, 403):
            raise SubtitleSkillError("DEPENDENCY_ERROR", f"DeepL rejected the API key (HTTP {status})")
        if status == 456:
            raise SubtitleSkillError("DEPENDENCY_ERROR", "DeepL quota exceeded (HTTP 456)")
        if status >= 400:
            text = raw.decode("utf-8", "replace")[:500]
            code = "INVALID_INPUT" if status == 400 else "TOOL_ERROR"
            raise SubtitleSkillError(code, f"DeepL {method} {path} failed (HTTP {status}): {text}")
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SubtitleSkillError("TOOL_ERROR", f"DeepL returned a non-JSON response for {path}") from exc
        if not isinstance(data, dict):
            raise SubtitleSkillError("TOOL_ERROR", f"DeepL returned an unexpected document for {path}")
        return data

    def translate(self, texts, source, target, glossary):
        source_code = source.split("-", 1)[0].upper()
        target_code = deepl_target_code(target)
        glossary_id = None
        if glossary:
            created = self._call(
                "POST",
                "/v2/glossaries",
                {
                    "name": "subtitle-skill",
                    "source_lang": source_code.lower(),
                    "target_lang": target.split("-", 1)[0].lower(),
                    "entries": "\n".join(f"{e.source}\t{e.target}" for e in glossary),
                    "entries_format": "tsv",
                },
            )
            glossary_id = created.get("glossary_id")
            if not isinstance(glossary_id, str) or not glossary_id:
                raise SubtitleSkillError("TOOL_ERROR", "DeepL did not return a glossary_id")
        try:
            out: List[str] = []
            for batch in _batches(list(texts), _DEEPL_BATCH_BYTES):
                payload: dict = {"text": batch, "source_lang": source_code, "target_lang": target_code}
                if glossary_id:
                    payload["glossary_id"] = glossary_id
                data = self._call("POST", "/v2/translate", payload)
                items = data.get("translations")
                if not isinstance(items, list) or len(items) != len(batch):
                    raise SubtitleSkillError(
                        "TOOL_ERROR", f"DeepL returned {len(items) if isinstance(items, list) else 'no'} translations for {len(batch)} texts"
                    )
                for item in items:
                    text = item.get("text") if isinstance(item, Mapping) else None
                    if not isinstance(text, str):
                        raise SubtitleSkillError("TOOL_ERROR", "DeepL returned a translation without text")
                    out.append(text)
            return out
        finally:
            if glossary_id:
                try:
                    self._call("DELETE", f"/v2/glossaries/{urllib.parse.quote(glossary_id)}", None)
                except SubtitleSkillError:
                    pass  # a leftover glossary is harmless; the translation already succeeded or failed


def _batches(texts: List[str], limit: int) -> List[List[str]]:
    batches: List[List[str]] = []
    cur: List[str] = []
    size = 0
    for text in texts:
        n = len(text.encode("utf-8")) + 16
        if cur and size + n > limit:
            batches.append(cur)
            cur, size = [], 0
        cur.append(text)
        size += n
    if cur:
        batches.append(cur)
    return batches


def make_engine(name: str, *, memory: Optional[Mapping[str, str]] = None) -> TranslationEngine:
    if name == "memory":
        return MemoryEngine(memory or {})
    if name == "argos":
        return ArgosEngine()
    if name == "deepl":
        return DeepLEngine()
    raise SubtitleSkillError("INVALID_INPUT", f"engine must be one of {list(ENGINES)}, got {name!r}")


def engine_status() -> dict:
    """What `doctor` reports: which engines can run here, without running them."""
    out = {}
    for name in ENGINES:
        engine = make_engine(name)
        ok, why = engine.available()
        entry = {"available": ok, "offline": name != "deepl"}
        if why:
            entry["detail"] = why
        out[name] = entry
    return out


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------


def _segment(cue: SubtitleCue, source_profile: Optional[LocaleProfile]) -> str:
    if source_profile is not None:
        return _join_lines(cue.text, source_profile)
    return " ".join(line.strip() for line in cue.text.split("\n") if line.strip())


def apply_glossary_to_source(text: str, glossary: Sequence[GlossaryEntry]) -> str:
    """Replace each source term with its target term, longest first, without
    re-matching inside text that an earlier (longer) term already produced."""
    if not glossary:
        return text
    pieces: List[Tuple[str, bool]] = [(text, False)]  # (text, already replaced)
    for entry in glossary:
        nxt: List[Tuple[str, bool]] = []
        for piece, done in pieces:
            if done or entry.source not in piece:
                nxt.append((piece, done))
                continue
            parts = piece.split(entry.source)
            for k, part in enumerate(parts):
                if part:
                    nxt.append((part, False))
                if k < len(parts) - 1:
                    nxt.append((entry.target, True))
        pieces = nxt
    return "".join(p for p, _ in pieces)


def glossary_misses(source: str, translated: str, glossary: Sequence[GlossaryEntry]) -> List[GlossaryEntry]:
    """Glossary entries whose source term is in `source` (longest-first,
    non-overlapping) but whose target term is not in `translated`."""
    misses: List[GlossaryEntry] = []
    remaining = source
    for entry in glossary:
        if entry.source in remaining:
            remaining = remaining.replace(entry.source, "\x00")
            if entry.target.casefold() not in translated.casefold():
                misses.append(entry)
    return misses


def _wrap_words(text: str, max_width: float) -> List[str]:
    """Greedy word wrap at spaces for languages without a locale profile.
    A single word longer than the line stays whole (validation reports it)."""
    words = text.split()
    lines: List[str] = []
    cur = ""
    for word in words:
        cand = word if not cur else cur + " " + word
        if len(cand) <= max_width or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


#: A line that ends with one of these is a natural place to break.
_BREAK_AFTER = frozenset(",.;:!?、。，．！？；：」』）)")


def _is_hiragana(ch: str) -> bool:
    return "\u3041" <= ch <= "\u309f"


def _is_kanji_or_katakana(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff" or "\u3400" <= ch <= "\u4dbf" or "\u30a0" <= ch <= "\u30ff"


def _break_penalty(head: str, tail: str, profile: Optional[LocaleProfile]) -> int:
    """0 at a natural break, 1 elsewhere. Natural: after punctuation, or (ja)
    where hiragana is followed by kanji/katakana -- after a particle such as
    は/に/を, the rough phrase (文節) boundary a Japanese subtitler breaks at.
    No dictionary is used, so this is a preference among legal breaks, never
    a rule."""
    if head and head[-1] in _BREAK_AFTER:
        return 0
    if profile is not None and profile.language == "ja" and head and tail:
        if _is_hiragana(head[-1]) and _is_kanji_or_katakana(tail[0]):
            return 0
    return 1


def _balanced_two_lines(text: str, profile: Optional[LocaleProfile], max_width: float) -> Optional[List[str]]:
    """The legal two-line break that fits, preferring a natural break
    (`_break_penalty`) and then the shortest longer line; None if none fits."""
    best = None
    if profile is None:
        words = text.split()
        for k in range(1, len(words)):
            a, b = " ".join(words[:k]), " ".join(words[k:])
            widest = max(len(a), len(b))
            score = (_break_penalty(a, b, None), widest)
            if widest <= max_width and (best is None or score < best[0]):
                best = (score, [a, b])
        return best[1] if best else None
    units = _tokenize(text, profile)
    for k in range(1, len(units)):
        head, tail = units[:k], units[k:]
        tail_units = [u for u in tail if u not in _SPACES]
        if not tail_units or not _legal_split(head, tail_units[0], profile):
            continue
        a, b = _render(head), _render(tail)
        if not a or not b:
            continue
        widest = max(_width([a], profile), _width([b], profile))
        score = (_break_penalty(a, b, profile), widest)
        if widest <= max_width and (best is None or score < best[0]):
            best = (score, [a, b])
    return best[1] if best else None


def layout_text(text: str, profile: Optional[LocaleProfile], max_width: float, max_lines: int) -> str:
    """Break translated text into lines for the target language."""
    text = text.strip()
    width = profile.text_width(text) if profile is not None else len(text)
    if width <= max_width:
        return text
    if max_lines >= 2:
        two = _balanced_two_lines(text, profile, max_width)
        if two is not None:
            return "\n".join(two)
    if profile is not None:
        return "\n".join(break_lines(text, profile, max_width))
    return "\n".join(_wrap_words(text, max_width))


@dataclass(frozen=True)
class TranslationResult:
    document: SubtitleDocument
    engine: str
    engine_version: str
    #: (cue_id, glossary entry) for every term missing from a translation
    glossary_misses: Tuple[Tuple[str, GlossaryEntry], ...]


def translate_document(
    document: SubtitleDocument,
    *,
    target_language: str,
    engine: TranslationEngine,
    glossary: Sequence[GlossaryEntry] = (),
    max_chars_per_line: float,
    max_lines: int,
    engine_version: Optional[str] = None,
) -> TranslationResult:
    source_profile = profile_for(document.language)
    target_profile = profile_for(target_language)
    segments = [_segment(cue, source_profile) for cue in document.cues]
    for cue, seg in zip(document.cues, segments):
        if len(seg) > TEXT_LIMIT:
            raise SubtitleSkillError("INVALID_INPUT", f"cue {cue.id}: text longer than {TEXT_LIMIT} characters")

    if isinstance(engine, MemoryEngine) or engine.native_glossary():
        engine_input = segments
    else:
        engine_input = [apply_glossary_to_source(s, glossary) for s in segments]

    translated = engine.translate(engine_input, document.language, target_language, glossary)
    if len(translated) != len(segments):
        raise SubtitleSkillError(
            "TOOL_ERROR", f"engine {engine.name!r} returned {len(translated)} translations for {len(segments)} cues"
        )

    cues = []
    misses: List[Tuple[str, GlossaryEntry]] = []
    for cue, seg, out in zip(document.cues, segments, translated):
        flat = " ".join(out.split()) if target_profile is None or target_profile.break_at_spaces else _join_lines(out.strip(), target_profile)
        if not flat.strip():
            raise SubtitleSkillError("TOOL_ERROR", f"engine {engine.name!r} returned an empty translation for cue {cue.id}")
        text = layout_text(flat, target_profile, max_chars_per_line, max_lines)
        for entry in glossary_misses(seg, flat, glossary):
            misses.append((cue.id, entry))
        metadata = dict(cue.metadata)
        metadata["translation"] = {"source_text": cue.text, "source_language": document.language}
        cues.append(replace(cue, text=text, metadata=metadata))

    version = engine_version or engine.version()
    doc_metadata = dict(document.metadata)
    doc_metadata["translation"] = {
        "engine": engine.name,
        "engine_version": version,
        "source_document_id": document.id,
        "source_language": document.language,
        "target_language": target_language,
        "glossary_sha256": sha256_json([[e.source, e.target] for e in glossary]) if glossary else None,
    }
    new_doc = SubtitleDocument(
        id=f"{document.id}.{target_language}",
        version=document.version,
        language=target_language,
        cues=tuple(cues),
        metadata=doc_metadata,
    )
    return TranslationResult(new_doc, engine.name, version, tuple(misses))
