!(import! &self (library OmegaClaw-Core ./plugins/openclaw/openclaw.py))

(= (openClawEnabled) (empty))
(= (openClawURL) (empty))
(= (openClawAgent) (empty))

(= (loadOmegaClawPlugin)
    (progn
        ; Enable or disable the integration.
        (configure openClawEnabled disabled)
        ; OpenClaw Gateway base URL, http://... or https://...
        ; In Docker this is only a fallback: the real target and its token
        ; are set at container start (openclaw_url=, OMEGACLAW_OPENCLAW_TOKEN)
        ; and injected by the local Nginx proxy. See plugins/openclaw/README.md.
        (configure openClawURL "")
        ; OpenClaw agent name. "main" by default
        (configure openClawAgent "main")
        (if (== (openClawEnabled) enabled)
            (if (== (openClawURL) "")
                (Error
                    (_loadOmegaClawPlugin)
                    "OpenClaw integration is enabled, but openClawURL is not configured")
                (progn
                    (log INFO "openclaw-plugin" "OpenClaw integration is enabled")
                    (add-skill
                        delegate-task-to-openclaw-agent
                        "Delegate a self-contained task to an external OpenClaw agent in a new separate session"
                        (task_in_quotes))))
            (log INFO "openclaw-plugin" "OpenClaw integration is disabled"))))

(= (delegate-task-to-openclaw-agent $message)
    (py-call (
        openclaw.send
        $message
        (openClawURL)
        (openClawAgent)
    )))