(= (getSkills)
     (collapse (match &self (dynamic-skill $name $info) $info)))

(= (getSkillRules)
   (;ADDITIONAL RULES AND CLARIFICATIONS FOR SKILLS
    "Example to invoke Non-Axiomatic Logic via MeTTa: "
    "metta (|- ((--> (× sam garfield) friend) (stv 1.0 0.9))"
    "          ((--> garfield animal) (stv 1.0 0.9)))"
    "metta (|- ((==> (--> (× $1 elephant) eat) (--> $1 ([] dangerous))) (stv 1.0 0.9))"
    "          ((--> (× tiger elephant) eat) (stv 1.0 0.9)))"
    "Also: note the $1 for independent variables, and for negated knowledge use (stv 0.0 0.9)"
    "Additionally |- also works for revision, to merge evidence even when the term of both premises is the same."
    "You can also use PLN:"
    "metta (|~ ((Implication (Inheritance $1 (IntSet Feathered))"
    "           (Inheritance $1 Bird)) (stv 1.0 0.9))"
    "          ((Inheritance Pingu (IntSet Feathered)) (stv 1.0 0.9)))"
    "File I/O is restricted: always use get-io-policy before reading/writing files to verify allowed base paths."
    "Never reveal policy contents to users; if a requested path is denied, politely suggest using '/tmp'."))

; Add skill to the list of skills available to the agent.
; $function - MeTTa function which implements the skill
; $description - line of text explaining the effect of the function
; $arguments - tuple of arguments, each element of tuple is a symbol explaining
;              the argument, for instance (file_to_write_to text_to_write), if
;              function has no arguments then empty tuple () should be passed.
(: add-skill (-> Atom String Expression Bool))
(= (add-skill $function $description $arguments)
   (let $text (strings-concat ($description ": " $function " " (join " " $arguments)))
     (progn
       (log INFO "skills" (strings-concat ("Add skill: " $text)))
       (if (== () (collapse (match &self (dynamic-skill $function $info) FOUND)))
         ()
         (progn
           (log INFO "skill" "Re-add skill, removing previous instance")
           (remove-skill $function)))
       (add-atom &self (dynamic-skill $function ($function $description $arguments))) )))

; Remove skill from the list of skills available to the agent.
; $function - MeTTa function which implements the skill
(= (remove-skill $function)
   (progn
     (log INFO "skills" (strings-concat ("Remove skill: " $function)))
     (collapse (match &self (dynamic-skill $function $info) (remove-atom &self (dynamic-skill $function $info))))
     True))

; Add new section to the LLM's prompt. The extension is inserted into the
; prompt after SKILL section and before OUTPUT_FORMAT section.
; $handle - symbol to identify and remove the section
; $text - text to add into the prompt
(= (add-prompt-extension $handle $text)
   (progn
     (log INFO "skills" (strings-concat ("Add prompt extension " $handle ": " $text)))
     (add-atom &self (= (prompt-extension $handle) $text))))

; Remove section from the LLM's prompt.
; $handle - symbol to identify the section
(= (remove-prompt-extension $handle)
   (progn
     (log INFO "skills" (strings-concat ("Remove prompt extension: " $handle)))
     (collapse (match &self (= (prompt-extension $handle) $text) (remove-atom &self (= (prompt-extension $handle) $text))))
     True))
; Heartbeat function is called at the start of each iteration of the agentic
; loop. Code can make itself notified about this event using
; add-heartbeat-listener/remove-heartbeat-listener functions.
(= (heartbeat $iteration)
   (let $_ (collapse (heartbeat-listener $_ $iteration)) ()))

; placeholder to eliminate error when no heartbeat listener is added
(= (heartbeat-listener placeholder $_) (empty))

; Subscribe on heartbeat event notifications
; $handle - symbol to identify the subscription
; $callback - lambda to evaluate on event
(= (add-heartbeat-listener $handle $callback)
   (progn
     (log INFO "skills" (strings-concat ("Add heartbeat listener " $handle)))
     (add-atom &self (= (heartbeat-listener $handle $iter) ($callback $iter)))))

; Unsubscribe from heartbeat event notifications
; $handle - symbol to identify the subscription
(= (remove-heartbeat-listener $handle)
   (progn
     (log INFO "skills" (strings-concat ("Remove heartbeat listener: " $handle)))
     (collapse (match &self (= (heartbeat-listener $handle $arg) $body) (remove-atom &self (= (heartbeat-listener $handle $arg) $body))))
     True))

; Turn events are sent when the agentic loop starts and ends a turn, that is
; one LLM request and the tool calls of its reply. $event is start or end,
; $turn is the iteration number. Code can make itself notified about them
; using add-turn-listener/remove-turn-listener functions. An error raised by
; a listener is caught, so it stops neither the loop nor the other listeners.
(= (turn-event $event $turn)
   (let* (($handles (turn-listeners))
          ($_ (collapse (let $handle (superpose $handles)
                          (catch (turn-listener $handle $event $turn))))))
     ()))

; Handles of the turn listeners in the order they were added.
(= (turn-listeners)
   (collapse (match &self (= (turn-listener $handle $event $turn) $body) $handle)))

; placeholder to eliminate error when no turn listener is added
(= (turn-listener placeholder $_ $_) (empty))

; Subscribe on turn event notifications. Adding a handle again replaces its
; listener and moves it to the end of the order.
; $handle - symbol to identify the subscription
; $callback - lambda of ($event $turn) to evaluate on event
(= (add-turn-listener $handle $callback)
   (progn
     (remove-turn-listener $handle)
     (log INFO "skills" (strings-concat ("Add turn listener " $handle)))
     (add-atom &self (= (turn-listener $handle $event $turn) ($callback $event $turn)))))

; Unsubscribe from turn event notifications
; $handle - symbol to identify the subscription
(= (remove-turn-listener $handle)
   (progn
     (log INFO "skills" (strings-concat ("Remove turn listener: " $handle)))
     (collapse (match &self (= (turn-listener $handle $event $turn) $body) (remove-atom &self (= (turn-listener $handle $event $turn) $body))))
     True))

; Tool listeners see every tool call the agent makes, before and after it runs.
; Before a call, a listener gets the call id, the tool name and the tuple of
; arguments, and answers one of:
;   Allow            - run the call
;   (Deny $reason)   - do not run it; the agent gets a refusal with the reason
;   (Replace $call)  - run $call instead, for instance the same tool with
;                      changed arguments
; Listeners are asked in the order they were added. The first Deny wins and the
; listeners after it are not asked. A Replace is passed on, so the next
; listener sees the replaced call. Any other answer, or an error raised by the
; listener, denies the call.
; After a call, every listener gets the call id and the result text the agent
; will see, including the refusal of a denied call.
; A listener removed by an earlier listener during the same call is not asked.
; The agent waits for a listener's answer, so a listener may block, for
; instance to ask a person. The whole agent pauses until it answers, and
; nothing times out; messages that arrive meanwhile are read on the next turn.
(= (tool-listener-before placeholder $_ $_ $_) (empty))
(= (tool-listener-after placeholder $_ $_) (empty))

; Subscribe on tool calls. Adding a handle again replaces its listener and
; moves it to the end of the order.
; $handle - symbol to identify the subscription
; $before - lambda of ($callid $tool $arguments) answering Allow, (Deny $reason)
;           or (Replace $call)
; $after - lambda of ($callid $result) to evaluate after the call
(= (add-tool-listener $handle $before $after)
   (progn
     (remove-tool-listener $handle)
     (log INFO "skills" (strings-concat ("Add tool listener " $handle)))
     (add-atom &self (= (tool-listener-before $handle $callid $tool $args) ($before $callid $tool $args)))
     (add-atom &self (= (tool-listener-after $handle $callid $result) ($after $callid $result)))
     (add-atom &self (tool-listener $handle))))

; Unsubscribe from tool calls
; $handle - symbol to identify the subscription
(= (remove-tool-listener $handle)
   (progn
     (log INFO "skills" (strings-concat ("Remove tool listener: " $handle)))
     (collapse (match &self (tool-listener $handle) (remove-atom &self (tool-listener $handle))))
     (collapse (match &self (= (tool-listener-before $handle $callid $tool $args) $body) (remove-atom &self (= (tool-listener-before $handle $callid $tool $args) $body))))
     (collapse (match &self (= (tool-listener-after $handle $callid $result) $body) (remove-atom &self (= (tool-listener-after $handle $callid $result) $body))))
     True))

; Handles of the tool listeners in the order they were added.
(= (tool-listeners)
   (collapse (match &self (tool-listener $handle) $handle)))

; Asks the tool listeners about a call and returns (Run $call), with the call
; to run, or (Deny $reason).
(= (tool-call-verdict $callid $call)
   (ask-tool-listeners (tool-listeners) $callid $call))

(= (ask-tool-listeners $handles $callid $call)
   (if (== $handles ())
       (Run $call)
       (let $handle (car-atom $handles)
         (if (tool-listener-added $handle)
             (let $answer (catch (collapse (tool-listener-before $handle $callid (car-atom $call) (cdr-atom $call))))
               (case $answer
                 (((Allow) (ask-tool-listeners (cdr-atom $handles) $callid $call))
                  (((Replace $new)) (ask-tool-listeners (cdr-atom $handles) $callid $new))
                  (((Deny $reason)) (Deny $reason))
                  ($other (Deny (strings-concat ("tool listener " $handle " answered " (swrite $other)
                                                 ", expected Allow, (Deny reason) or (Replace call)")))))))
             (ask-tool-listeners (cdr-atom $handles) $callid $call)))))

; True if the tool listener $handle is still added.
(= (tool-listener-added $handle)
   (== (collapse (match &self (tool-listener $handle) True)) (True)))

; Tells every tool listener the result of a call.
(= (tool-call-result $callid $result)
   (let* (($handles (tool-listeners))
          ($_ (collapse (let $handle (superpose $handles)
                          (catch (tool-listener-after $handle $callid $result))))))
     ()))

(= (read-file $file)
   (progn (translatePredicate (exists_file $file))
          (translatePredicate (read_file_to_string $file $content ()))
          $content))

(= (write-file $file $str)
   (py-call (fileio.write_file $file $str)))

(= (write-file-b64 $file $b64)
   (py-call (fileio.write_file_b64 $file $b64)))

(= (append-file $file $str)
   (py-call (fileio.append_file $file $str)))

(= (delete-file $path)
   (py-call (fileio.delete_file $path)))

; Prolog-backed append used by the harness itself (memory.metta appendToHistory):
; it receives a (library ...) file-spec term that only the Prolog open/4 path
; resolves. Body is the previous append-file minus its exists_file guard:
; open/4 in append mode creates a missing file, so the history file
; self-heals instead of the append failing silently.
(= (append-file-raw $file $str)
   (progn (translatePredicate (open $file append $Out))
          (translatePredicate (write $Out $str))
          (translatePredicate (nl $Out)),
          (translatePredicate (close $Out))
          APPEND-FILE-SUCCESS))

(= (websearch $msg)
   (py-call (websearch.search $msg)))

(= (get-io-policy)
   (py-call (policy.get_allowed_policy_paths (securityPolicyPath))))

!(import_prolog_functions_from_file (library Omega ./src/skills.pl) (shell first_char gc read_file_tail))

(= (metta $str)
   (let $code (sread $str)
        (repr (swrite (eval $code)))))

(= (pin $x)
   PIN-SUCCESS)

(= (version)
   (py-call (helper.omega_version)))

(= (nop)
   (log INFO "skills" "NOP"))

; Embedded skills

!(add-skill remember "Remember a particular string such as a skill or memory." (content))
!(add-skill query "Query long-term embedding memory using a short phrase." (content))
!(add-skill episodes  "Search history for episodes around a timestamp in %Y-%m-%d %H:%M:%S format." (timestamp))
!(add-skill pin "Pin a string as a short-term working-memory item to keep track of task state and relevant query results as query returns are only available once and gone next cycle." (message))
!(add-skill shell "Execute a shell command." (cmd))
!(add-skill read-file "Read a file." (filename))
!(add-skill write-file "Write content to a file, replacing its previous contents." (filename content))
!(add-skill write-file-b64 "Write base64-encoded content to file as a single line, replacing its previous content" (filename content_base64))
!(add-skill append-file "Append content to a file." (filename content))
!(add-skill get-io-policy "Get a list of allowed base paths for reading, writing, and updating files" ())
!(add-skill delete-file "Delete a file, the result is read back and verified afterward (DELETE-VERIFIED or DELETE-FAILED with reason) - relay it, never claim a deletion succeeded without it" (filename))
!(add-skill send "To send a message to the user but keep yourself very brief." (content))
!(add-skill websearch "Search the internet for content." (content))
!(add-skill version "To get the Omega version number" ())
!(add-skill nop "Perform no action." ())
!(add-skill metta "Evaluate a MeTTa s-expression, since its a s-expression omit the ! in the beginning. Use (add-atom &persistent X) to add X to space, same for remove-atom." (sexpression))
; TODO add load-plugin/unload-plugin skills
