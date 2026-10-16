!(import! &self ./tests/lib/utils)
!(import! &self ../src/helper.py)
!(import! &self ./src/utils)
!(import! &self ./src/skills)

; Memory path and test channels used by the channel prompt registration test.
(= (memoryDirectory) (joinPath ((projectRootDirectory) "memory")))
!(import! &self ./res/prompt_channel.py)

; used by prompt-extension
!(import! &self ./src/loop)

!(test (progn
         (add-skill test-skill "This is a test skill" (test_arg))
         (expression-count-item (getSkills) (test-skill "This is a test skill" (test_arg))))
       1)

!(test (progn
         (add-skill test-skill-2 "This is a test skill" (test_arg))
         (add-skill test-skill-2 "This is a test skill" (test_arg))
         (expression-count-item (getSkills) (test-skill-2 "This is a test skill" (test_arg))))
       1)

!(test (progn
         (remove-skill test-skill)
         (expression-count-item (getSkills) (test-skill "This is a test skill" (test_arg))))
       0)

!(test (progn
         (add-prompt-extension test-prompt "TEST PROMPT EXTENSION")
         (contains-text (getPromptExtensions) "TEST PROMPT EXTENSION"))
       True)

!(test (progn
         (remove-prompt-extension test-prompt)
         (contains-text (getPromptExtensions) "TEST PROMPT EXTENSION"))
       False)

!(test (progn
         (py-call (prompt_channel.start "with-prompt" "tg_prompt.txt"))
         (addChannelPromptExtension)
         (contains-text (getPromptExtensions) "TELEGRAM ROUTING:"))
       True)

!(test (progn
         (remove-prompt-extension channel-prompt)
         (contains-text (getPromptExtensions) "TELEGRAM ROUTING:"))
       False)

; A channel without a prompt file adds nothing, even under the telegram id.
!(test (progn
         (py-call (prompt_channel.start "telegram" ""))
         (addChannelPromptExtension)
         (contains-text (getPromptExtensions) "TELEGRAM ROUTING:"))
       False)

(= (on-heartbeat $iter)
   (let $count (get-state &test_heartbeat_counter)
     (change-state! &test_heartbeat_counter (+ $count $iter))))

!(test (progn
         (change-state! &test_heartbeat_counter 0)
         (add-heartbeat-listener test-heartbeat (|-> ($iter) (on-heartbeat $iter)))
         (heartbeat 1)
         (heartbeat 2)
         (remove-heartbeat-listener test-heartbeat)
         (get-state &test_heartbeat_counter))
       3)

!(test (progn
         (change-state! &test_heartbeat_counter 0)
         (add-heartbeat-listener test-heartbeat (|-> ($iter) (on-heartbeat $iter)))
         (remove-heartbeat-listener test-heartbeat)
         (heartbeat 1)
         (get-state &test_heartbeat_counter))
       0)

!(test (read-file "does-not-exist") (empty))
