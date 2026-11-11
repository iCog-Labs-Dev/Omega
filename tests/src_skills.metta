!(import! &self ./tests/lib/utils)
!(import! &self ../src/helper.py)
!(import! &self ./src/utils)
!(import! &self ./src/skills)

; Define the selected channel and memory path used by the Telegram prompt
; registration test.
(= (commchannel) telegram)
(= (memoryDirectory) (joinPath ((projectRootDirectory) "memory")))

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
         (addTelegramPromptExtension)
         (contains-text (getPromptExtensions) "TELEGRAM ROUTING:"))
       True)

!(test (progn
         (remove-prompt-extension tg-prompt)
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

; Tool and turn listeners. test-tool stands in for a skill and counts its runs.
; Calls are built with cons-atom, so they stay data and are not run early.
(= (test-tool $x)
   (progn (change-state! &test_tool_runs (+ 1 (get-state &test_tool_runs)))
          (strings-concat ("ran " $x))))

(= (test-call $x) (cons-atom test-tool ($x)))

; The result text of a call run without listeners, as the loop computed it before.
(= (plain-tool-result $call)
   (catch (let $R (collapse (eval $call)) (py-call (helper.normalize_string $R)))))

(= (reset-tool-test)
   (progn (change-state! &test_tool_runs 0)
          (change-state! &test_tool_seen ())
          (change-state! &test_tool_after ())
          ()))

(= (record-tool-call $callid $tool $args)
   (change-state! &test_tool_seen (append (get-state &test_tool_seen) (($callid $tool $args)))))

(= (record-tool-result $callid $result)
   (change-state! &test_tool_after (append (get-state &test_tool_after) (($callid $result)))))

; No listener: the call runs and gives the same result as before.
!(test (progn (reset-tool-test)
              (== (runToolCall call-1 (test-call "a")) (plain-tool-result (test-call "a"))))
       True)

; Allow: the listener sees the call id, tool name and arguments, and the call runs.
!(test (progn (reset-tool-test)
              (add-tool-listener test-allow
                                 (|-> ($id $tool $args) (progn (record-tool-call $id $tool $args) Allow))
                                 (|-> ($id $result) ()))
              (let $rc (runToolCall call-1 (test-call "a"))
                (progn (remove-tool-listener test-allow)
                       ((contains-text $rc "ran a") (get-state &test_tool_seen) (get-state &test_tool_runs)))))
       (True ((call-1 test-tool ("a"))) 1))

; Deny: the call does not run and the refusal carries the reason.
!(test (progn (reset-tool-test)
              (add-tool-listener test-deny (|-> ($id $tool $args) (Deny "not now")) (|-> ($id $result) ()))
              (let $rc (runToolCall call-1 (test-call "a"))
                (progn (remove-tool-listener test-deny)
                       ($rc (get-state &test_tool_runs)))))
       ((Error (test-call "a") "Refused: not now") 0))

; The model gets the refusal as a failed call, the way the loop reports errors.
!(test (progn (reset-tool-test)
              (change-state! &error ())
              (add-tool-listener test-deny (|-> ($id $tool $args) (Deny "not now")) (|-> ($id $result) ()))
              (let $rc (runToolCall call-1 (test-call "a"))
                (progn (remove-tool-listener test-deny)
                       (if (HandleError $rc $error) $error ()))))
       "ALERT_FAILED(Error (test-tool \"a\") \"Refused: not now\")")

; Replace: the replacement runs, and the next listener sees the replacement.
!(test (progn (reset-tool-test)
              (add-tool-listener test-replace (|-> ($id $tool $args) (Replace (test-call "b"))) (|-> ($id $result) ()))
              (add-tool-listener test-allow
                                 (|-> ($id $tool $args) (progn (record-tool-call $id $tool $args) Allow))
                                 (|-> ($id $result) ()))
              (let $rc (runToolCall call-1 (test-call "a"))
                (progn (remove-tool-listener test-replace)
                       (remove-tool-listener test-allow)
                       ((contains-text $rc "ran b") (get-state &test_tool_seen)))))
       (True ((call-1 test-tool ("b")))))

; The first Deny wins: listeners added after it are not asked.
!(test (progn (reset-tool-test)
              (add-tool-listener test-deny (|-> ($id $tool $args) (Deny "first")) (|-> ($id $result) ()))
              (add-tool-listener test-deny-2
                                 (|-> ($id $tool $args) (progn (record-tool-call $id $tool $args) (Deny "second")))
                                 (|-> ($id $result) ()))
              (let $rc (runToolCall call-1 (test-call "a"))
                (progn (remove-tool-listener test-deny)
                       (remove-tool-listener test-deny-2)
                       ($rc (get-state &test_tool_seen)))))
       ((Error (test-call "a") "Refused: first") ()))

; An answer that is not Allow, Deny or Replace denies the call.
!(test (progn (reset-tool-test)
              (add-tool-listener test-bad (|-> ($id $tool $args) maybe) (|-> ($id $result) ()))
              (let $rc (runToolCall call-1 (test-call "a"))
                (progn (remove-tool-listener test-bad)
                       ((car-atom $rc) (get-state &test_tool_runs)))))
       (Error 0))

; Adding a handle again moves its listener to the end of the order.
!(test (progn (reset-tool-test)
              (add-tool-listener test-deny (|-> ($id $tool $args) (Deny "first")) (|-> ($id $result) ()))
              (add-tool-listener test-deny-2 (|-> ($id $tool $args) (Deny "second")) (|-> ($id $result) ()))
              (add-tool-listener test-deny (|-> ($id $tool $args) (Deny "first again")) (|-> ($id $result) ()))
              (let $rc (runToolCall call-1 (test-call "a"))
                (progn (remove-tool-listener test-deny)
                       (remove-tool-listener test-deny-2)
                       $rc)))
       (Error (test-call "a") "Refused: second"))

; A listener removed by an earlier listener during the call is not asked.
!(test (progn (reset-tool-test)
              (add-tool-listener test-remover
                                 (|-> ($id $tool $args) (progn (remove-tool-listener test-deny) Allow))
                                 (|-> ($id $result) ()))
              (add-tool-listener test-deny (|-> ($id $tool $args) (Deny "no")) (|-> ($id $result) ()))
              (let $rc (runToolCall call-1 (test-call "a"))
                (progn (remove-tool-listener test-remover)
                       ((contains-text $rc "ran a") (get-state &test_tool_runs)))))
       (True 1))

; After a call, every listener gets the call id and the result.
!(test (progn (reset-tool-test)
              (add-tool-listener test-after (|-> ($id $tool $args) Allow) (|-> ($id $result) (record-tool-result $id $result)))
              (tool-call-result call-1 "SUCCESS, RETURN: ran a")
              (remove-tool-listener test-after)
              (tool-call-result call-2 "SUCCESS, RETURN: ran b")
              (get-state &test_tool_after))
       ((call-1 "SUCCESS, RETURN: ran a")))

; A removed listener is not asked.
!(test (progn (reset-tool-test)
              (add-tool-listener test-deny (|-> ($id $tool $args) (Deny "no")) (|-> ($id $result) ()))
              (remove-tool-listener test-deny)
              (contains-text (runToolCall call-1 (test-call "a")) "ran a"))
       True)

; A listener that blocks only delays the call.
!(test (progn (reset-tool-test)
              (add-tool-listener test-slow (|-> ($id $tool $args) (progn (sleep 1) Allow)) (|-> ($id $result) ()))
              (let $rc (runToolCall call-1 (test-call "a"))
                (progn (remove-tool-listener test-slow)
                       (contains-text $rc "ran a"))))
       True)

; Turn listeners get start and end with the turn number.
!(test (progn (change-state! &test_turns ())
              (add-turn-listener test-turn
                                 (|-> ($event $turn) (change-state! &test_turns (append (get-state &test_turns) (($event $turn))))))
              (turn-event start 3)
              (turn-event end 3)
              (remove-turn-listener test-turn)
              (turn-event start 4)
              (get-state &test_turns))
       ((start 3) (end 3)))

(= (record-turn $x) (change-state! &test_turns (append (get-state &test_turns) ($x))))

(= (raising-turn-listener $event $turn) (/ 1 0))

; An error in a turn listener is caught, and the other listeners still run.
!(test (progn (change-state! &test_turns ())
              (add-turn-listener test-raise raising-turn-listener)
              (add-turn-listener test-turn (|-> ($event $turn) (record-turn ($event $turn))))
              (turn-event start 5)
              (remove-turn-listener test-raise)
              (remove-turn-listener test-turn)
              (get-state &test_turns))
       ((start 5)))

; Adding a turn listener's handle again replaces it and moves it to the end.
!(test (progn (change-state! &test_turns ())
              (add-turn-listener test-a (|-> ($event $turn) (record-turn old-a)))
              (add-turn-listener test-b (|-> ($event $turn) (record-turn b)))
              (add-turn-listener test-a (|-> ($event $turn) (record-turn a)))
              (turn-event start 6)
              (remove-turn-listener test-a)
              (remove-turn-listener test-b)
              (get-state &test_turns))
       (b a))
