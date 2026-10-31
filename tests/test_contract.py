"""`provides[]` tool ids use the short `subtitle/` prefix (docs/adr/ADR-002-provides-tool-id-prefix.md)."""
from subtitle_skill import CONTRACT_VERSION, SKILL_ID
from subtitle_skill.contract import OPERATIONS, TOOL_ID_PREFIX, build_contract


def test_provides_tool_ids_use_the_short_subtitle_prefix():
    provides = build_contract()["provides"]
    assert provides == [
        {"id": "subtitle.generate", "lifecycle": "EXPERIMENTAL", "tool_id": "subtitle/generate"},
        {"id": "subtitle.render", "lifecycle": "EXPERIMENTAL", "tool_id": "subtitle/render"},
        {"id": "subtitle.translate", "lifecycle": "EXPERIMENTAL", "tool_id": "subtitle/translate"},
        {"id": "subtitle.mux", "lifecycle": "EXPERIMENTAL", "tool_id": "subtitle/mux"},
    ]


def test_tool_id_prefix_is_not_the_skill_id():
    """Only the tool ids change: the Skill's identity, which video-production-agent's
    check_contract() and its response checks compare exactly, stays `subtitle-skill`."""
    contract = build_contract()
    assert TOOL_ID_PREFIX == "subtitle"
    assert SKILL_ID == "subtitle-skill"
    assert contract["skill_id"] == "subtitle-skill"
    assert contract["contract_version"] == CONTRACT_VERSION == "1.0.0"
    assert not any(e["tool_id"].startswith(SKILL_ID + "/") for e in contract["provides"])


def test_every_tool_id_names_a_real_operation():
    """`<prefix>/<op>`: the part after the prefix is the `operation` value a `run` request
    sends, so a caller can map a tool id to a request without a lookup table."""
    contract = build_contract()
    for entry in contract["provides"]:
        prefix, op = entry["tool_id"].split("/")
        assert prefix == TOOL_ID_PREFIX
        assert op in OPERATIONS
        assert op in contract["operations"]
    assert sorted(e["tool_id"].split("/")[1] for e in contract["provides"]) == sorted(OPERATIONS)
