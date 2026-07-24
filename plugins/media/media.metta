; media plugin — registers the media skills (describe-image, generate-image)
; backed by the media_handler Python module. Loaded via the `metta` loader
; because skill registration (add-skill) is only available to MeTTa plugins.
;
; media_handler resolves on the Python path because the telegram_media python
; plugin (same location, {REPO}/plugins/media) is loaded at startup and appends
; that directory to sys.path. describe-image/generate-image are only meaningful
; alongside that channel, so the coupling is intentional.

(= (loadOmegaClawPlugin)
   (progn
     (add-skill describe-image
                "See/read an image the user attached. Call when a message shows [image] and you need its contents; optionally pass what to look for, e.g. describe-image what error is shown"
                (query))
     (add-skill generate-image
                "Generate an image and send it to the user. Use when the user asks you to create, draw, generate, paint, or show a picture or image. Pass a vivid, detailed English description, e.g. generate-image a red panda astronaut floating in space, studio lighting"
                (description))
     ()))

(= (describe-image $query)
   (py-call (media_handler.describe_image $query)))

(= (generate-image $prompt)
   (py-call (media_handler.generate_and_send $prompt)))
