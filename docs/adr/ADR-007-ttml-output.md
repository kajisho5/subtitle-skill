# ADR-007: TTML Output as IMSC 1.1 Text Profile and EBU-TT-D

Status: Accepted

## Context

`ECOSYSTEM_ROADMAP.md` step 9 (field review 2026-11-05): `generate` and `translate` write TTML (IMSC 1.1 Text Profile) and EBU-TT-D besides SRT and WebVTT, keeping WebVTT cue positions and regions where the format can carry them, each output validated against its schema. Done when a ja/en fixture round-trips SRT → TTML → SRT with the same cues and the TTML validates. ARIB STD-B24/B36 binary streams are not made (ffmpeg-skill#426); TTML is the replacement.

Read on 2026-11-06:

1. W3C IMSC 1.1 (Recommendation): the Text Profile designator `http://www.w3.org/ns/ttml/profile/imsc1.1/text` in `ttp:contentProfiles`; an IMSC 1.1 document is a TTML2 document. W3C publishes no separate IMSC XSD; the TTML2 XML Schema is in the TTML2 repository (`w3c/ttml2`, `spec/xsd/`, W3C Document License).
2. EBU Tech 3380 (EBU-TT-D) and its XSD (`ebu/ebu-tt-d-xsd`, master): `ttp:timeBase` and `xml:lang` required on `tt`; `head` must hold `styling` and `layout`; every `region` needs `xml:id`, `tts:origin`, `tts:extent`; `p` needs `xml:id`; times match `[0-9][0-9]+:[0-5][0-9]:[0-5][0-9](\.[0-9]+)?`; `span` holds text and `br` only; `p` and `span` take no `tts:` attributes (styles by reference); colours `#rrggbb` / `#rrggbbaa`. The repository has no licence file (only `styling.xsd` carries an Apache-2.0 header).

## Decision

1. **Two new formats**, `ttml` (IMSC 1.1 Text) and `ebu-tt-d`, in `formats.SUPPORTED_FORMATS`, so `generate` and `translate` take them. `render` and `mux` stay SRT: caption.py burns and muxes SRT/ASS only.
2. **One writer for both** (`formats/ttml.py`): `ttp:timeBase="media"`, clock times `hh:mm:ss.fff`, regions in percent (no `tts:extent` on `tt`, no pixels), every style a named `style` referenced by id (allowed in both; required by EBU-TT-D). IMSC adds `ttp:contentProfiles`; EBU-TT-D adds `ttp:cellResolution="32 15"` and `ebuttm:documentMetadata/ebuttm:conformsToStandard` = `urn:ebu:tt:distribution:2018-04`.
3. **Positions as regions.** The default region is the centre 80 % x 80 % (`tts:origin="10% 10%"`, `tts:extent="80% 80%"`, `tts:displayAlign="after"`). WebVTT `line` → the region's top edge with `displayAlign="before"`; `position`/`size` → its horizontal centre and width; `align` → `tts:textAlign`. `bold`/`italic`/`color` → `tts:fontWeight`/`tts:fontStyle`/`tts:color` in a named style on the cue's span. Speakers stay a `Speaker: ` prefix, as in SRT and WebVTT.
4. **Cue ids as NCNames.** `xml:id` is `cue-` + the id with every character outside `[A-Za-z0-9.-]` written `_xHHHH_`, so `1` and `c 2` are valid ids and read back exactly.
5. **Checked before writing.** `validate_ttml` (standard library only) checks the profile's structural rules: root, namespaces, `timeBase`, the IMSC designator or the EBU-TT-D standard, `head`/`styling`/`layout`, region geometry inside the root container, defined region and style references, `p` timing and ids, allowed elements, EBU-TT-D's no-inline-styling and span content rules. A document that fails is never written (`OUTPUT_ERROR`). The response and sidecar carry `conformance: {profile, valid, checked}`.
6. **Schema validation in the tests.** The W3C TTML2 XSD is vendored as a test fixture (`tests/fixtures/schemas/ttml2/`, W3C Document License, unmodified); `tests/test_ttml.py` validates the ja and en fixtures and a positioned document in both profiles with lxml (installed in CI as a test dependency; the package itself needs nothing new). The EBU-TT-D XSD is not vendored: its repository states no licence. It was run locally instead: the ja and en fixtures and the positioned document validate against `ebu/ebu-tt-d-xsd` master (with `xml.xsd` from w3.org) — and this check found the first draft's inline span styling, which EBU-TT-D does not allow.
7. **Reading back.** `parse_ttml` reads either profile (regions back to `line`/`position`/`size`, named styles back to `bold`/`italic`/`color`); `parse_srt` reads SubRip. Both exist for round trips and tests; there is no `convert` operation.

## Done when, and how it was checked

- `tests/fixtures/ttml/ja.srt` and `en.srt` (three cues each: two-line text, a speaker prefix, an italic cue, `&` and `<...>` in the text) round-trip SRT → TTML → SRT byte for byte in both profiles, with the same cue dicts in between.
- The same documents validate against the W3C TTML2 XSD in CI (both profiles) and against the EBU-TT-D XSD locally.

## Consequences

- IMSC 1.1's own constraints beyond TTML2's schema (for example at most four regions shown at once) are not all checked. The writer creates one region per distinct cue position, so a document with more than four positions active at the same time would exceed it; ordinary documents use one or two.
- The TTML2 schema is a superset check for EBU-TT-D; the stricter EBU-TT-D XSD runs only when someone runs it by hand, as here.
- The contract's `formats`, `capabilities.formats` and the generate/translate descriptions change (`conformance?` output, `ttml_profiles`).
