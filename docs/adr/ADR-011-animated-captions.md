# ADR-011: Animated Captions Are Passed Through to ffmpeg-skill `caption`

Status: Accepted

## Context

Everything made from subtitle cues belongs to subtitle-skill, animated captions included (ECOSYSTEM_ROADMAP step 12; the owner's ownership decision O11). Burned captions did not move: ffmpeg-skill's `caption` tool already has `--animate none|fade|pop|slide` and `--karaoke --karaoke-style sweep|word`, but `render` never sent them. Effects timed to the words of a transcript beyond those flags (remotion-ffmpeg-skill `Karaoke`, `Emphasis`) are remotion-ffmpeg-skill's job and stay out of this skill.

What `caption.py` does with the flags, read in its source and run at 2.24.0 (the vendored test fixture) and 3.8.3 (main at the time of writing):

- With `--srt` (or `--text`), an animation makes it generate an ASS file of its own and burn that. fade is `\fad(200,200)`; pop is `\fad(80,120)` with a scale from 60 % through 110 % to 100 % over 200 ms; slide is `\fad(150,150)` with a `\move` up 30 script lines over 250 ms. The file goes to `--write-ass PATH`, or to `<output>.ass` next to the output by default.
- With `--ass`, the given file is burned as it is: `--animate` / `--karaoke` are ignored, with no warning.
- `--mode mux` with either flag is refused (`--animate/--karaoke render pixels into the picture and require --mode burn`).
- `--karaoke` takes each word's time from `<srt stem>.words.json` (or `<srt stem>.json`) next to the SRT it burns, as `{"words": [{"word", "start", "end"}]}`. A cue's words are used only when exactly as many timed words fall inside the cue as the cue has space-separated words. Otherwise the cue falls back to `--karaoke-timing`: `energy` (the default, read from the audio's loudness) or `even`.
- When caption.py's own layout changes the cues (a cue lengthened to `--min-duration`, default 1 s, or split at `--max-lines`), it burns `<output stem>_adjusted.srt` and looks for the words beside that file, not beside the SRT it was given. With only `<srt stem>.words.json`, a 0.5 s cue lengthened to 1 s was highlighted in three even thirds (`\kf33` ×3) instead of from its words (`\kf10 \kf5 \kf85`).
- `--karaoke-style word` writes one event per word, and puts the entrance animation on the first word's event only. `\fad` fades out at the end of its own event, so with an entrance the whole caption fades out at the end of the first word and is back for the second. Measured on the decoded frames: with `pop` and a 0.2 s first word the caption's brightness drops to 16 % of full before the second word; with `fade` or `slide` and a 0.6 s first word it drops to 5 % and 12 %. `--karaoke-style sweep` is one event per cue and does not blink.

## Decision

1. **`render` takes `animate` and `karaoke`** (both opt-in). `animate` is one of `none`, `fade`, `pop`, `slide`; `karaoke` is `sweep` or `word`. They become `--animate MODE` and `--karaoke --karaoke-style STYLE --karaoke-timing even`. Nothing is drawn by subtitle-skill. An unknown value is `INVALID_INPUT` with `details.field`.
2. **Without either field, or with only `animate: "none"`, the burn is the call it was before step 12**: the same argv, the same render identity (an earlier render is reused), and no `animation` in the response.
3. **`karaoke` needs the cues' `words`.** A cue without `words` is `INVALID_INPUT`, `details.field` `subtitle.cues[N].words`. So is a cue whose drawn text (with a speaker prefix and ruby readings, as burned) does not split into as many space-separated words as it has `words`: caption.py would ignore its timings and split it evenly. The words are written as `<srt stem>.words.json` beside the temporary SRT. They are also written as `<output stem>_adjusted.words.json`, for the case above where caption.py adjusts the cues. `--karaoke-timing even` is sent so that a cue caption.py still cannot time from its words (a cue it splits at `--max-lines`) is split evenly, the same way every time, instead of from the audio.
4. **Refused with the reason, never drawn static** (`INVALID_INPUT`, `details.field` and `details.conflicts_with`):
   - **A soft track** (`mode: "mux"`): only a burn can move.
   - **A burn through subtitle-skill's own ASS**: `placement`, `regional_profile`, cue `ruby` and `caption_profile: "shorts"`. That ASS goes through `caption --ass`, which ignores the animation flags, so the burn would be silently static. Carrying the animation into that ASS would mean writing the `\fad` / `\move` / `\kf` tags here, which is drawing the animation in this skill. It would be a second copy of caption.py's animation code that would drift from it. A later step can lift this if caption.py ever animates a given ASS.
   - **`animate` with `karaoke: "word"`**: the blink above. `karaoke: "sweep"` with `animate` works, and so does `karaoke: "word"` alone.
5. **The values used are in the result.** The response and sidecar carry `animation: {animate, karaoke, word_timings, engine_flags}`. `word_timings` is `"cue.words"` with karaoke, and `engine_flags` is the exact caption flags sent, without the temporary `--write-ass` path. `engine_flags` is part of the render identity.
6. **Side files are this burn's own, and are removed.** If the ASS or either `.words.json` path already exists, the burn is refused before anything is written (`INVALID_INPUT`, `details.field` `output_path`, `details.existing`). An existing file is never replaced or deleted, and caption.py never reads it as this request's word timings. caption.py writes its ASS to `--write-ass` beside the temporary SRT. That file and the `.words.json` files this skill wrote are deleted after the burn, whether it succeeded or failed. `<output stem>_adjusted.srt` is caption.py's own side file, written for every burn whose cues it adjusts, animated or not, and is left as before.
7. **A caption tool without `--animate`, `--karaoke`, `--karaoke-style`, `--karaoke-timing` and `--write-ass` is `DEPENDENCY_ERROR`.** This is decided by reading the located `caption.py`. A request without the fields is unaffected.
8. **The contract change is additive**: `render` inputs `animate?`, `karaoke?`; output `animation?`; `operations.render.animation` (the values and the flags); `capabilities.animated_captions`; `parameters.render` gains both names. Nothing is removed or renamed. `contract_version` is unchanged. video-production-agent pins the contract byte for byte, so it re-pins before it can pass the fields.

## Done when, and how it was checked

`tests/test_animation.py` renders a 320×180, 25 fps lavfi clip through the vendored caption.py and real ffmpeg, then reads the decoded frames back. The fixture is `tests/fixtures/animation/talk_words_en.json`, one cue from 1.0 to 2.6 s with three timed words.

- **Each `animate` mode**: nothing on the cue's first frame. Between 5 % and 60 % of full brightness inside the entrance (1.08 s fade and slide, 1.04 s pop). Full by 1.3 s, and gone after the cue. slide's caption is lower inside the entrance than after it. A static burn is full on its first frame.
- **`karaoke: "sweep"`**: no highlight on the first frame, part of the line highlighted at 1.3 s, all of it by 2.56 s. At 1.45 s little white is left, because MOVE and WITH end by 1.4 s from `words`; an even split would still be inside MOVE.
- **`karaoke: "word"`**: at 1.12 s the first word is highlighted and nothing is past. At 1.45 s the last word is active and the first two are drawn white.
- A 0.5 s cue that caption.py lengthens to 1 s is still timed from its words.
- sweep with pop never drops below 80 % of its brightness after the entrance.
- No `.ass` or `.words.json` is left in the workspace.
- **Refused**: karaoke without words (`subtitle.cues[0].words`), karaoke with a speaker prefix that adds a drawn word, an animation on a mux, an animation with placement, ruby or `caption_profile`, `animate` with `karaoke: "word"`, and unknown values.
- **Unchanged**: with a recording fake caption.py, the argv with no fields, with `None`, and with `animate: "none"` is the argv before step 12. With the real one, `animate: "none"` reuses the plain render. A fake caption.py without the flags gives `DEPENDENCY_ERROR`.

The same file passes against ffmpeg-skill 3.8.3 (`SUBTITLE_SKILL_TEST_FFMPEG_SKILL_SRC`), and the daily compatibility check runs it against main. The burn tests need ffmpeg: they run in `ci.yml`'s `real-engine` job and wherever ffmpeg is installed.

## Consequences

- Animated captions are only as good as caption.py's. Its look (timings, colours FFD200 / B4B4B4, scale 112 %) is not configurable through `render` beyond `style.color` / `style.bold` / `style.size`, which still go to `--color` / `--bold` / `--size`. With karaoke, `--color` is the not-yet-spoken colour.
- Faces, regional profiles, ruby and shorts cannot move yet (point 4). A caller who wants both picks one.
- Japanese and other scripts written without spaces can be karaoke'd only when the cue text has spaces between its `words`, because caption.py splits on spaces.
- A cue caption.py splits at `--max-lines` may be timed evenly instead of from its words (point 3).
- The `_adjusted.words.json` copy depends on caption.py's side-file name. If it changes, karaoke on adjusted cues falls back to an even split; the adjusted-cue test catches that in the daily check.
