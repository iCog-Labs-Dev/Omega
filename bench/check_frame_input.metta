; Regression check for two runtime bugs that made the benchmark unrunnable. Needs Docker
; and a provider key, so it is not part of the free host test suite. Run it as:
;
;   docker run --rm -t --env-file .env \
;       -v $PWD/bench/check_frame_input.metta:/PeTTa/run.metta:ro \
;       omegaclaw:bench-frame provider=OpenAI embeddingprovider=Local
;
; It must be mounted at /PeTTa/run.metta, not somewhere under /tmp: the library loader
; resolves its relative imports against the script's own directory and silently swallows
; the failures, so a script outside /PeTTa loads nothing and every call evaluates to
; itself, which looks like a pass.
;
; Read the two CASE_PROMPT lines. Expected once fixed:
;   CASE1_PROMPT  contains SENTINELONE
;   CASE2_PROMPT  contains SENTINELONE and SENTINELTWO
;   IS_SELF       (((FINAL text) -> true) ((version) -> false))
; Before the fix CASE2_PROMPT was byte-identical to CASE1_PROMPT and SENTINELTWO never
; appeared anywhere.
;
; 1. A message must reach the acting prompt even when it is not the first message.
;    The prompt renders only the current frame, and a new frame is loaded as current
;    only while root mode is Slow, i.e. for the first message and never again.
; 2. An undefined command head must not evaluate to itself silently, which is what let
;    the model keep re-emitting a nonexistent FINAL command with no correction signal.

!(import! &self (library lib_import))
!(git-import! "https://github.com/asi-alliance/OmegaClaw-Core.git")
!(import! &self (library OmegaClaw-Core lib_omegaclaw))

; Same startup as (omegaclaw 1), minus initChannels / commChannelSend so nothing needs a
; bus. The provider is started because ingesting a second message composes frame
; relations, which calls the model.
!(initConfig)
!(initLoop)
!(initLogger)
!(applySecurityPolicy)
!(initMemory)
!(initKnowledge)
!(initPlugins)
!(initContextFrame)
!(llmProviderStart (provider))

!(println! (INIT_ROOTMODE (cfv2-root-mode)))

; --- 1: first message. No current frame yet, so the new frame is loaded and its goal
; is the message itself.
!(ctx-ingest-user-message "SENTINELONE first message from user")
!(println! (AFTER_FIRST_ROOTMODE (cfv2-root-mode)))
!(println! (AFTER_FIRST_FRAMEID (get-state &cfv2-current-frame-id)))
!(println! (CASE1_PROMPT (contextFrameForPrompt)))

; --- 2: second message. Root mode is Fast now, so the frame created from this message
; is NOT loaded. Without the fix the prompt still shows only SENTINELONE.
!(ctx-ingest-user-message "SENTINELTWO addressed to me must be visible")
!(println! (AFTER_SECOND_ROOTMODE (cfv2-root-mode)))
!(println! (AFTER_SECOND_FRAMEID (get-state &cfv2-current-frame-id)))
!(println! (CASE2_PROMPT (contextFrameForPrompt)))

; --- 3: the command path as loop.metta runs it. Commands arrive as data from sread, so
; $s is an unevaluated atom and (== (eval $s) $s) compares atoms rather than two
; re-evaluations of the same expression. FINAL is undefined and must come back true;
; version is a real skill and must come back false.
!(println! (IS_SELF (let $sexpr (sread "((FINAL text) (version))") (collapse (let $s (superpose $sexpr) ($s -> (== (eval $s) $s)))))))
