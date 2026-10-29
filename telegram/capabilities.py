"""What the channel profile lets the model do, and the check that the profile
says nothing the code does not do.

callable_capabilities is an allowlist. Each enabled capability names the skills
it grants, and when the channel starts every other skill is refused, including
any core or another plugin adds later. A capability's constraints apply to the
skills it grants; same_chat_only is the only one, and it confines send to the
chat being answered.

The profile used to read like a policy that nothing enforced: it forbade code
execution while production ran 13,906 shell commands. So the channel refuses to
start on a profile holding a key or a constraint this code does not act on, and
the file cannot drift from the code again without someone noticing.
"""

KNOWN_CONSTRAINTS = frozenset({"same_chat_only"})

# Every key the plugin reads. A dict lists the keys allowed under it; None
# means the value is used as it is.
SCHEMA = {
    "profile_name": None,
    "description": None,
    "telegram": {
        "reply_only_when_directly_tagged": None,
        "reply_on_reply_to_bot": None,
        "restrict_to_config_chat": None,
        "allowed_chats": None,
        "allow_group_bots": None,
        "dm_support": {"enabled": None},
        "reply_constraints": {
            "allow_files": None,
            "allow_media": None,
            "allow_audio": None,
            "allow_image_generation": None,
            "allow_pdf_generation": None,
            "allow_pdf_font_install": None,
            "allow_voice_reply": None,
        },
    },
    "admin_controls": {"admin_ids": None},
    "classification_model": {"name": None},
    "spam_protection": {
        "time_window": None,
        "message_limit": None,
        "cooldown_duration": None,
        "admin_alert_threshold": None,
    },
    "ethics_pass": {"blocked_categories": None},
    "callable_capabilities": None,  # checked by _capability_problems
}

CAPABILITY_KEYS = frozenset({"enabled", "skills", "constraints"})


def _unknown_keys(value, schema, path):
    if not isinstance(value, dict):
        return [f"{'.'.join(path)} should be a mapping"]
    problems = []
    for key, sub in value.items():
        here = path + (str(key),)
        if key not in schema:
            problems.append(f"{'.'.join(here)} is not read by the plugin")
        elif isinstance(schema[key], dict) and sub is not None:
            problems.extend(_unknown_keys(sub, schema[key], here))
    return problems


def _capability_problems(capabilities):
    if not isinstance(capabilities, dict) or not capabilities:
        return ["callable_capabilities is missing or empty, so no skill would be granted"]
    problems = []
    for name, cap in capabilities.items():
        where = f"callable_capabilities.{name}"
        if not isinstance(cap, dict):
            problems.append(f"{where} should be a mapping")
            continue
        for key in cap:
            if key not in CAPABILITY_KEYS:
                problems.append(f"{where}.{key} is not read by the plugin")
        if not isinstance(cap.get("enabled"), bool):
            problems.append(f"{where}.enabled should be true or false")
        skills = cap.get("skills")
        if not (isinstance(skills, list) and skills and all(isinstance(s, str) for s in skills)):
            problems.append(f"{where}.skills should list the skills it grants")
        for constraint in cap.get("constraints") or []:
            if constraint not in KNOWN_CONSTRAINTS:
                problems.append(f"{where} has constraint {constraint!r}, which the plugin does not enforce")
    return problems


def problems(profile):
    """Everything in the profile the plugin would not act on, as readable lines.
    Empty means the profile says only what the code does."""
    if not isinstance(profile, dict):
        return ["the profile is empty or not a mapping"]
    return (_unknown_keys(profile, SCHEMA, ())
            + _capability_problems(profile.get("callable_capabilities")))


def _enabled(profile):
    capabilities = profile.get("callable_capabilities") or {}
    return [cap for cap in capabilities.values()
            if isinstance(cap, dict) and cap.get("enabled") is True]


def granted_skills(profile):
    """The skills the enabled capabilities grant."""
    return frozenset(skill for cap in _enabled(profile) for skill in cap.get("skills") or [])


def constraints_for(profile, skill):
    """The constraints on a skill, from every enabled capability granting it."""
    return frozenset(c for cap in _enabled(profile)
                     if skill in (cap.get("skills") or [])
                     for c in cap.get("constraints") or [])
