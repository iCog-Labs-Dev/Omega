; The media skills (describe-image, generate-image) are visible in getSkills.
;
; They are registered statically in src/skills.metta rather than by the
; plugins/media plugin itself. Reason, verified on SWI-Prolog 10.0.2 + PeTTa
; v1.0.4 (the versions pinned in the repo Dockerfile):
;
;   A MeTTa plugin's loadOmegaClawPlugin cannot register skills. loadPlugin
;   reports success (returns ()) but nothing lands in getSkills. Tried
;   (a) add-skill in the plugin, (b) the same plus importing ./src/skills into
;   the plugin, and (c) a raw (add-atom &self (= (dynamic-skill ...) ...)) —
;   in every case the atoms are in neither &self nor the plugin's own
;   &plugin-<name> space. Called directly from &self, add-skill works, and
;   core's tests/src_skills.metta and tests/src_plugin.metta both pass on the
;   same image, so the runtime itself is fine.
;
; Move these two skills into the plugin (and assert loadPlugin here instead)
; once a plugin can register skills that reach getSkills.
;
; Run:
;   docker build -t omegaclaw:test .
;   docker run --rm --entrypoint sh -v "$PWD":/PeTTa/repos/OmegaClaw-Core:ro \
;     -w /PeTTa/repos/OmegaClaw-Core -e PETTA_PATH=/PeTTa omegaclaw:test \
;     -c "sh /PeTTa/run.sh tests/smoke_media_plugin.metta"

!(import! &self ./tests/lib/utils)
!(import! &self ./src/utils)
!(import! &self ./src/skills)

!(test (expression-count-item (getSkills)
         "- See/read an image the user attached. Call when a message shows [image] and you need its contents; optionally pass what to look for, e.g. describe-image what error is shown: describe-image query")
       1)

!(test (expression-count-item (getSkills)
         "- Generate an image and send it to the user. Use when the user asks you to create, draw, generate, paint, or show a picture or image. Pass a vivid, detailed English description, e.g. generate-image a red panda astronaut floating in space, studio lighting: generate-image description")
       1)
