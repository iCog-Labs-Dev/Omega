;; Telegram channel plugin.
;;
;; Registers the aiogram-based communication channel, the two media skills that
;; come with it, and the channel's own prompt section. Everything the plugin
;; adds is registered here at load time, so core needs no telegram-specific
;; code of its own.

;; No import! of the .py files is needed: the loader puts this folder on
;; sys.path, and py-call imports a module on first use. Keeping the plugin free
;; of (library Omega ./plugins/...) paths is what lets it live outside
;; the core tree.
;;
;; The channel module is NOT called telegram.py. The plugin folder is appended
;; to sys.path rather than prepended, so a module named telegram would lose to
;; the python-telegram-bot package whenever that is installed.

; Ask the plugin's own module where its files are. Deriving the path from the
; repository root instead would tie the plugin to living inside the core tree.
(= (pluginTelegramPromptPath)
   (py-call (telegram_channel.prompt_path)))

(= (loadOmegaPlugin)
   (progn
     (log INFO "telegram-plugin" "Registering Telegram communication channel")
     (let $rc (py-call (telegram_channel.loadOmegaPlugin)) ())
     (add-skill describe-image
                "See/read an image the user attached. Call when a message shows [image] and you need its contents. The argument says what to look for, such as: what error is shown; it may be empty"
                (image_query))
     (add-skill generate-image
                "Generate an image and send it to the user. Use when the user asks you to create, draw, generate, paint, or show a picture or image. The argument is a vivid, detailed English description, such as: a red panda astronaut floating in space, studio lighting. The image is generated and sent for you"
                (image_description))
     (add-skill generate-pdf
                "Generate a PDF document and send it to the user. Use when the user asks for a PDF containing text, a summary, or a report. The argument is the complete text of the document, line breaks included"
                (pdf_content))
     (add-skill install-pdf-font
                "Install a font so generate-pdf can draw a script it cannot yet, such as Devanagari, Thai, Chinese or emoji. Use when generate-pdf answers PDF_FAILED naming characters it cannot draw. The argument is a Google Fonts family name, not a URL, such as: Noto Sans Devanagari. The download runs in the background and a message starting [install-pdf-font] arrives when it ends"
                (font_family))
     (add-skill speak
                "Speak a reply aloud as Telegram voice messages. Long text is split and sent in order. Use when the user asks for speech. The argument is the exact text to speak"
                (voice_text))
     (addTelegramPromptSection)
     ()))

;; The channel talks to the public, so it carries scope limits the default
;; prompt has no reason to include. Added as a prompt section rather than by
;; replacing the prompt file, so it composes with whichever provider prompt the
;; host is already using.
(= (addTelegramPromptSection)
   (if (exists-file (pluginTelegramPromptPath))
       (add-prompt-extension telegram-guardrails (read-file (pluginTelegramPromptPath)))
       (log WARN "telegram-plugin"
            (strings-concat ("No prompt section at " (pluginTelegramPromptPath) ", skipping")))))

(= (describe-image $query)
   (py-call (media_handler.describe_image $query)))

(= (generate-image $prompt)
   (py-call (media_handler.generate_and_send $prompt)))

(= (speak)
   (speak ""))

(= (speak $text)
   (py-call (media_handler.speak $text)))


(= (generate-pdf $content)
   (py-call (media_handler.generate_and_send_pdf $content)))

(= (install-pdf-font $family)
   (py-call (media_handler.install_pdf_font $family)))
