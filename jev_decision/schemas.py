"""Versioned tool contracts shared by discovery, validation and packaging tests."""
from __future__ import annotations

TEXT = {"type": "string", "minLength": 1, "pattern": r"\S"}
DESCRIPTION = {"oneOf": [TEXT, {"type": "object", "minProperties": 1},
                           {"type": "array", "minItems": 1}]}
PROBABILITY = {"type": "number", "minimum": 0, "maximum": 1}
NULLABLE_NUMBER = {"type": ["number", "null"]}
NULLABLE_TEXT = {"type": ["string", "null"]}
HASH = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
NOUL_CRITERIA = {"type": "object", "minProperties": 1,
                 "properties": {"true": DESCRIPTION, "false": DESCRIPTION}, "additionalProperties": False}
CHOICE_CRITERIA = {"type": "object", "minProperties": 2, "maxProperties": 255,
                   "propertyNames": TEXT, "additionalProperties": {"anyOf": [DESCRIPTION, {"type": "null"}]}}
SCORE_CRITERIA = {"type": "array", "minItems": 2, "maxItems": 10, "uniqueItems": True, "items": DESCRIPTION}


def _question(kind, criteria=None):
    properties = {"type": {"const": kind}, "instructions": DESCRIPTION}
    required = ["type", "instructions"]
    if criteria is not None:
        properties["criteria"] = criteria
        if kind != "noul":
            required.append("criteria")
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


NATIVE_QUESTION = {"oneOf": [_question("noul", NOUL_CRITERIA), _question("choice", CHOICE_CRITERIA),
                             _question("score", SCORE_CRITERIA)]}


def _legacy_question(kind, criteria):
    properties = {"id": {**TEXT, "maxLength": 200}, "type": {"const": kind},
                  "prompt": DESCRIPTION, "instructions": DESCRIPTION, "criteria": criteria}
    requirements = [{"anyOf": [{"required": ["prompt"]}, {"required": ["instructions"]}]}]
    if kind == "choice":
        properties["options"] = {"type": "array", "minItems": 2, "maxItems": 255,
                                  "uniqueItems": True, "items": TEXT}
        requirements.append({"anyOf": [{"required": ["criteria"]}, {"required": ["options"]}]})
    if kind == "score":
        properties["scale"] = SCORE_CRITERIA
        requirements.append({"anyOf": [{"required": ["criteria"]}, {"required": ["scale"]}]})
    return {"type": "object", "properties": properties, "required": ["id", "type"],
            "additionalProperties": False, "allOf": requirements}


QUESTIONS = {"oneOf": [
    {"type": "object", "minProperties": 1, "maxProperties": 128,
     "propertyNames": {**TEXT, "maxLength": 200}, "additionalProperties": NATIVE_QUESTION},
    {"type": "array", "minItems": 1, "maxItems": 128,
     "items": {"oneOf": [_legacy_question("noul", NOUL_CRITERIA),
                          _legacy_question("choice", CHOICE_CRITERIA),
                          _legacy_question("score", SCORE_CRITERIA)]}},
]}
QUESTIONS["description"] = ("Prefer a JSON array of question objects with unique id, type, instructions, and descriptive "
                            "criteria where required. Native ID-keyed question objects are also accepted. Never encode JSON as a string.")
QUESTIONS["examples"] = [[
    {"id": "failure", "type": "noul", "instructions": "Does the excerpt report a failed check?"},
    {"id": "kind", "type": "choice", "instructions": "What does the excerpt primarily report?",
     "criteria": {"failure": "A check failed", "success": "A check passed"}},
    {"id": "relevance", "type": "score", "instructions": "How relevant is the excerpt to the stated goal?",
     "criteria": ["Unrelated detail", "Useful context", "Required evidence"]},
]]
STATE = {**DESCRIPTION, "description": "Prefer a JSON object with relevant facts/excerpts; do not encode an object as a string.",
         "examples": [{"excerpt": "FAILED: one authentication check", "goal": "Find the failed check"}]}

# Clients can include tool schemas in model context. The advertised jev_decide
# schema uses the compact preferred form; the server still validates against the complete schema
# above, which also accepts native ID-keyed maps and legacy prompt/options/scale.
_COMPACT_TEXT = {"type": ["string", "object", "array"], "minLength": 1, "pattern": r"\S",
                 "minProperties": 1, "minItems": 1}


def _compact_question(kind, criteria, criteria_required):
    properties = {"id": {**TEXT, "maxLength": 200}, "type": {"const": kind},
                  "instructions": _COMPACT_TEXT, "criteria": criteria}
    return {"type": "object", "properties": properties, "additionalProperties": False,
            "required": ["id", "type", "instructions"] + (["criteria"] if criteria_required else [])}


COMPACT_QUESTIONS = {
    "type": "array", "minItems": 1, "maxItems": 128,
    "items": {"anyOf": [
        _compact_question("noul", {"type": "object", "minProperties": 1, "additionalProperties": False,
                                   "properties": {"true": _COMPACT_TEXT, "false": _COMPACT_TEXT}}, False),
        _compact_question("choice", {"type": "object", "minProperties": 2, "maxProperties": 255,
                                     "propertyNames": TEXT,
                                     "additionalProperties": {"anyOf": [_COMPACT_TEXT, {"type": "null"}]}}, True),
        _compact_question("score", {"type": "array", "minItems": 2, "maxItems": 10,
                                    "uniqueItems": True, "items": _COMPACT_TEXT}, True),
    ]},
    "description": "Array of questions with unique id. choice criteria map each label to its meaning; "
                   "score criteria list 2-10 ordered level descriptions. Never encode JSON as a string.",
    "examples": QUESTIONS["examples"],
}
COMPACT_STATE = {**_COMPACT_TEXT, "description": STATE["description"], "examples": STATE["examples"]}
USAGE = {"type": "object", "properties": {
    "input_tokens": {"type": ["integer", "null"], "minimum": 0},
    "output_tokens": {"type": ["integer", "null"], "minimum": 0}},
    "required": ["input_tokens", "output_tokens"], "additionalProperties": False}
METADATA = {
    "status": {"type": "string"}, "source": {"type": ["string", "null"]},
    "requested_model": NULLABLE_TEXT, "resolved_model": NULLABLE_TEXT, "usage": USAGE,
    "latency_ms": NULLABLE_NUMBER, "attempts": {"type": ["integer", "null"], "minimum": 0},
    "request_id": NULLABLE_TEXT, "is_fallback": {"type": ["boolean", "null"]},
    "error_code": NULLABLE_TEXT, "advisory_only": {"type": "boolean"},
}
DECISION_PROPERTIES = {
    "type": {"enum": ["noul", "choice", "score"]}, "probability": PROBABILITY,
    "confidence": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
    "selected": TEXT, "score": {"type": "number"}, "legend": {"type": "object"},
    "probabilities": {"type": "object", "additionalProperties": PROBABILITY},
}
BATCH_OUTPUT = {"type": "object", "properties": {
    **METADATA, "decisions": {"type": "object", "additionalProperties": {
        "type": "object", "properties": DECISION_PROPERTIES, "required": ["type"]}},
    "request_id": {"type": "string"}, "is_fallback": {"type": "boolean"}},
    "required": ["status"]}
MODE = {"type": "string", "enum": ["off", "shadow", "select"], "default": "off",
        "description": "Off never scores. Shadow scores but retains. Select requires a configured qualified profile."}
SOURCE_CLASS = {"type": "string", "enum": ["auto", "unknown", "test_log", "build_log", "application_log", "jsonl", "diff"]}
WORKLOAD = {"type": "object", "properties": {key: TEXT for key in (
    "harness", "harness_version", "primary_model", "primary_provider")},
    "required": ["harness", "harness_version", "primary_model", "primary_provider"], "additionalProperties": False,
    "description": "Actual caller workload identity; selection retains evidence unless it matches the qualified deployment."}
STATS = {"type": "object", "description": "Selection provenance, protected/retained line spans, usage and bypass reasons.",
         "properties": {**METADATA, "mode": MODE, "pruned": {"type": "boolean"},
                        "input_sha256": HASH, "source_sha256": HASH, "source_class": SOURCE_CLASS,
                        "source_start_line": {"type": "integer"}, "prompt_rubric_sha256": HASH,
                        "pruning_enabled": {"type": "boolean"}, "production_qualified": {"type": "boolean"},
                        "calls": {"type": "integer", "minimum": 0}, "planned_calls": {"type": "integer", "minimum": 0},
                        "qualification_report_sha256": HASH, "threshold_score": {"type": "number"},
                        "threshold_confidence": PROBABILITY,
                        "original_lines": {"type": "integer"}, "saved_lines": {"type": "integer"},
                        "original_bytes": {"type": "integer"}, "returned_bytes": {"type": "integer"},
                        "spans": {"type": "array", "items": {"type": "object", "properties": {
                            "start_line": {"type": "integer"}, "end_line": {"type": "integer"},
                            "retained": {"type": "boolean"}, "protected": {"type": "boolean"},
                            "assessed": {"type": "boolean"}, "score": NULLABLE_NUMBER, "confidence": NULLABLE_NUMBER}}}}}
SOURCE_REF = {"type": "object", "properties": {"source_path": TEXT, "source_sha256": HASH,
    "text_sha256": HASH, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}}
EVIDENCE_OUTPUT = {"type": "object", "properties": {
    "status": {"type": "string"}, "error_code": NULLABLE_TEXT, "output": {"type": "string"},
    "source_path": TEXT, "source_sha256": HASH, "source_ref": SOURCE_REF, "stats": STATS,
    "source_class": SOURCE_CLASS,
    "original_bytes": {"type": "integer"}, "redacted": {"type": "boolean"},
    "original_preserved": {"type": "boolean"}, "page": {"type": "object", "properties": {
        "start_line": {"type": "integer"}, "end_line": {"type": "integer"},
        "total_lines": {"type": "integer"}, "next_line": {"type": ["integer", "null"]},
        "has_more": {"type": "boolean"}, "max_bytes": {"type": "integer"}, "max_lines": {"type": "integer"}}}}}
_DOLLARS = {"type": ["number", "string"], "description": "Exact decimal string only when a finite float cannot represent the amount."}
STATUS_OUTPUT = {"type": "object", "properties": {
    "version": {"type": "string"}, "endpoint": TEXT, "model": TEXT, "home": TEXT,
    "timeout_s": {"type": "number"}, "max_request_bytes": {"type": "integer"}, "max_response_bytes": {"type": "integer"},
    "daily_budget_usd": {"type": "string"}, "timezone": TEXT,
    "workspace_roots": {"type": "array", "items": TEXT}, "enabled": {"type": "boolean"},
    "pruning_enabled": {"type": "boolean"}, "setup_complete": {"type": "boolean"}, "credential_source": TEXT,
    "key_env": TEXT, "selection_mode": MODE, "qualified_profile_path": NULLABLE_TEXT, "harness_target": NULLABLE_TEXT,
    "harness_scope": {"enum": ["user", "project"]}, "project_root": NULLABLE_TEXT,
    "credential_present": {"type": ["boolean", "null"]}, "authenticated": {"const": False},
    "authentication_status": {"const": "not_checked"}, "client_invocation_verified": {"const": False},
    "advisory_only": {"const": True}, "status": {"type": "string"}, "error_code": NULLABLE_TEXT,
    "credential": {"type": "object", "properties": {
        "managed_present": {"type": "boolean"}, "environment_present": {"type": "boolean"},
        "credential_present": {"type": ["boolean", "null"]}, "source": TEXT, "configured_source": TEXT,
        "presence_status": TEXT, "authentication_verified": {"const": False}}},
    "budget": {"type": "object", "properties": {
        **{key: TEXT for key in ("day", "timezone", "configured_timezone", "starts_at", "resets_at", "accounting", "status")},
        **{key: _DOLLARS for key in ("daily_limit_usd", "committed_usd", "known_spend_usd", "held_usd", "remaining_usd", "reservation_usd", "rate_per_million_usd")},
        **{key: {"type": "integer", "minimum": 0} for key in ("attempts", "pending_attempts", "unknown_attempts", "settled_attempts", "max_tokens_per_attempt")}}}}}


def _tool(name, description, properties, required, output):
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties, "required": required,
                            "additionalProperties": False},
            "outputSchema": output,
            "annotations": {"readOnlyHint": True, "destructiveHint": False,
                            "idempotentHint": False, "openWorldHint": name != "jev_status"}}


FULL_TOOLS_MANIFEST = [
    _tool("jev_status", "Inspect local configuration and budget. Does not authenticate or contact the provider.", {}, [],
          STATUS_OUTPUT),
    _tool("jev_decide", "Ask bounded descriptive Noul, Choice or Score questions. Advice cannot grant permission or certify execution.",
          {"state": STATE, "questions": QUESTIONS}, ["state", "questions"], BATCH_OUTPUT),
    _tool("jev_guard_command", "Assess command effects as advice; never execute or authorize a command.",
          {"command": TEXT, "cwd": {"type": "string"}}, ["command"],
          {"type": "object", "properties": {**METADATA, "risk_category": TEXT,
           "category_probabilities": {"type": ["object", "null"], "additionalProperties": PROBABILITY},
           "risk_probability": NULLABLE_NUMBER, "permission_authority": {"const": "native_harness"}}}),
    _tool("jev_verify_completion", "Assess gaps in supplied verification evidence; never certify task completion.",
          {"goal": TEXT, "recent_actions": {"type": "string"}, "last_output": {"type": "string"}},
          ["goal", "recent_actions", "last_output"], {"type": "object", "properties": {
           **METADATA, "support_probability": NULLABLE_NUMBER, "verification_gap_probability": NULLABLE_NUMBER,
           "verification_authority": {"const": "recorded_execution_evidence"}}}),
    _tool("jev_prune_output", "Measure relevance of already ingested text. Use jev_read_evidence before ingestion for possible savings. Unrecoverable text is retained.",
          {"raw_output": {"type": "string"}, "current_goal": TEXT, "mode": MODE, "source_class": SOURCE_CLASS,
           "max_retained_lines": {"type": "integer", "minimum": 1, "default": 100}},
          ["raw_output", "current_goal"], {"type": "object", "properties": {
           "pruned_output": {"type": "string"}, "stats": STATS, "status": {"type": "string"}, "error_code": NULLABLE_TEXT}}),
    _tool("jev_read_evidence", "Read UTF-8 evidence inside approved roots with recoverable original line references. Omission needs a qualified profile; changed hashes reject recovery.",
          {"path": TEXT, "goal": TEXT, "mode": MODE, "source_class": SOURCE_CLASS, "workload": WORKLOAD,
           "start_line": {"type": "integer", "minimum": 1, "default": 1},
           "max_lines": {"type": "integer", "minimum": 1, "maximum": 10000, "default": 1000},
           "max_bytes": {"type": "integer", "minimum": 1, "maximum": 65536, "default": 65536},
           "expected_source_sha256": HASH, "max_retained_lines": {"type": "integer", "minimum": 1, "default": 100}},
          ["path", "goal"], EVIDENCE_OUTPUT),
]

# Server-side validation uses the complete schemas; discovery advertises compact ones.
INPUT_VALIDATION_SCHEMAS = {tool["name"]: tool["inputSchema"] for tool in FULL_TOOLS_MANIFEST}
TOOLS_MANIFEST = [
    dict(tool, inputSchema={**tool["inputSchema"], "properties": {"state": COMPACT_STATE, "questions": COMPACT_QUESTIONS}})
    if tool["name"] == "jev_decide" else tool
    for tool in FULL_TOOLS_MANIFEST
]
