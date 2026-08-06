(= (ad-list-modules)
   (py-call (modules.list_modules_formatted)))

;; Directive hot cache
(= (ad-clear-last-directive)
   (progn
      (change-state! &ad-last-target    "")
      (change-state! &ad-last-task      "")
      (change-state! &ad-last-gate      "")
      (change-state! &ad-last-criteria  "")
      (change-state! &ad-last-priority  0.0)
      (change-state! &ad-last-output    "")
      (change-state! &ad-last-code      "")
      (change-state! &ad-last-admitted  False)
      (change-state! &ad-last-reason    "")
      (change-state! &ad-last-attempts  0)
      AD-DIRECTIVE-CACHE-CLEARED))

(= (ad-record-last-directive $target $task $gate $criteria $priority
                              $output $admitted $reason $attempts)
   (progn
      (change-state! &ad-last-target    $target)
      (change-state! &ad-last-task      $task)
      (change-state! &ad-last-gate      $gate)
      (change-state! &ad-last-criteria  $criteria)
      (change-state! &ad-last-priority  $priority)
      (change-state! &ad-last-output    $output)
      (change-state! &ad-last-admitted  $admitted)
      (change-state! &ad-last-reason    $reason)
      (change-state! &ad-last-attempts  $attempts)
      AD-DIRECTIVE-RECORDED))

(= (ad-last-directive)
   (LastDirective
      (target    (get-state &ad-last-target))
      (task      (get-state &ad-last-task))
      (gate      (get-state &ad-last-gate))
      (criteria  (get-state &ad-last-criteria))
      (priority  (get-state &ad-last-priority))
      (output    (get-state &ad-last-output))
      (code      (get-state &ad-last-code))
      (admitted  (get-state &ad-last-admitted))
      (reason    (get-state &ad-last-reason))
      (attempts  (get-state &ad-last-attempts))))

(= (ad-result-output
      (DirectiveResult (output $output) (admitted $admitted) (reason $reason) (attempts $attempts)))
   $output)

(= (ad-result-admitted
      (DirectiveResult (output $output) (admitted $admitted) (reason $reason) (attempts $attempts)))
   $admitted)

(= (ad-result-reason
      (DirectiveResult (output $output) (admitted $admitted) (reason $reason) (attempts $attempts)))
   $reason)

(= (ad-result-attempts
      (DirectiveResult (output $output) (admitted $admitted) (reason $reason) (attempts $attempts)))
   $attempts)

;; Execution
(= (ad-record-to-frame $note $variant $metrics $admitted)
   (if (== (cfv2-has-current-frame) True)
       (if (== $admitted True)
           (progn
              (cfv2-record-frame-note DirectiveAdmitted $note)
              (cfv2-add-result $variant $metrics))
           (cfv2-record-frame-note DirectiveRejected $note))
       _))

;; Task values follow the lowercase task names advertised in the prompt.
(= (ad-execution-eligible $task $gate $admitted)
   (and (== $task "generate")
        (and (== $gate "python-syntax")
             (== $admitted True))))

;; Directive policy owns which frame data a module may receive.
(= (ad-context-for-slice deliverables)
   (py-str ("deliverables: " (get-state &cfv2-current-deliverables))))

(= (ad-context-for-slice results)
   (py-str ("results: " (get-state &cfv2-current-results))))

(= (ad-context-for-slice history-summary)
   (py-str ("history-summary: " (get-state &cfv2-current-history-summary))))

(= (ad-context-for-slice results,deliverables)
   (py-str ("results: " (get-state &cfv2-current-results) (newline)
            "deliverables: " (get-state &cfv2-current-deliverables))))

(= (ad-context-for-slice deliverables,results)
   (py-str ("deliverables: " (get-state &cfv2-current-deliverables) (newline)
            "results: " (get-state &cfv2-current-results))))

(= (ad-effective-slice $target $slice)
   (if (== $target "researcher") deliverables $slice))

(= (ad-max-attempts $target $priority)
   (if (== $target "researcher") 1
       (if (>= $priority 1.0) 3 1)))

(= (execute-last-code)
   (if (== (get-state &ad-last-target) "")
       (progn
          (ad-record-to-frame
             "target=python-executor task=execute admitted=False attempts=0 reason=no code to execute"
             ExecutionOutput
             "output= attempts=0"
             False)
          (DirectiveResult (admitted False) (output "") (reason "no code to execute") (attempts 0)))
   (if (== (ad-execution-eligible
               (get-state &ad-last-task)
               (get-state &ad-last-gate)
               (get-state &ad-last-admitted)) True)
   (let* (($raw      (get-state &ad-last-output))
          ($code     (py-call (helper.strip_code_fences (repr $raw))))
          ($result   (sread (py-call (modules.run_directive
                                 (repr "python-executor")
                                 (repr $code)
                                 (repr "execute")
                                 (repr "exec-success")
                                 (repr "")
                                 (repr 1)))))
          ($output   (ad-result-output $result))
          ($admitted (ad-result-admitted $result))
          ($reason   (ad-result-reason $result))
          ($attempts (ad-result-attempts $result)))
      (progn
         (change-state! &ad-last-code $code)
         (ad-record-last-directive
            "python-executor" "execute" "exec-success" "" 1.0
            $output $admitted $reason $attempts)
         (ad-record-to-frame
            (py-str ("target=python-executor task=execute"
                     " admitted=" $admitted
                     " attempts=" $attempts
                     " reason=" $reason))
            ExecutionOutput
            (py-str ("output=" $output " attempts=" $attempts))
            $admitted)
         (DirectiveResult
            (admitted $admitted)
            (output   $output)
            (reason   $reason)
            (attempts $attempts))))
       (progn
          (ad-record-to-frame
             "target=python-executor task=execute admitted=False attempts=0 reason=last directive is not admitted Python code"
             ExecutionOutput
             "output= attempts=0"
             False)
          (DirectiveResult (admitted False) (output "")
                           (reason "last directive is not admitted Python code") (attempts 0))))))

;; Internal parser-recovery function — not exposed in SKILL_SET.
;; Emitted by balance_parentheses when dispatch-directive receives wrong arity.
(= (directive-format-error $reason)
   (DirectiveResult
      (admitted False)
      (output   "")
      (reason   $reason)
      (attempts 0)))

(= (dispatch-directive $target $task $gate $criteria $priority $slice)
   (let* (($effective-slice (ad-effective-slice $target $slice))
          ($context  (ad-context-for-slice $effective-slice))
          ($attempts (ad-max-attempts $target $priority))
          ($result   (sread (py-call (modules.run_directive
                                 $target $context $task $gate $criteria $attempts))))
          ($output   (ad-result-output $result))
          ($admitted (ad-result-admitted $result))
          ($reason   (ad-result-reason $result))
          ($attempts (ad-result-attempts $result)))
      (progn
         (change-state! &ad-last-code "")
         (ad-record-last-directive
            $target $task $gate $criteria $priority
            $output $admitted $reason $attempts)
         (ad-record-to-frame
            (py-str ("target=" $target " task=" $task
                     " gate=" $gate
                     " admitted=" $admitted
                     " attempts=" $attempts
                     " reason=" $reason))
            DirectiveOutput
            (py-str ("target=" $target " task=" $task
                     " output=" $output
                     " attempts=" $attempts))
            $admitted)
         (DirectiveResult
            (admitted $admitted)
            (output   $output)
            (reason   $reason)
            (attempts $attempts)))))

;; Agent-callable skills
(= (getDirectiveModules)
   (py-str
      ("AVAILABLE_DIRECTIVE_MODULES:" (newline)
       (ad-list-modules) (newline)
       "AVAILABLE_GATES: python-syntax | metta-syntax | math-result | nonempty | exec-success | research-quality | task-consistency | passthrough" (newline)
       "AVAILABLE_TASKS: generate | critique | revise | execute | evaluate" (newline)
       "MATH_RESULT: criteria is a quoted arithmetic expression; output must end with FINAL_ANSWER: <number>" (newline)
       "FORMAT: dispatch-directive target task gate \"criteria\" priority slice  (criteria must be quoted; slice is deliverables, results, history-summary, or results,deliverables)")))
(= (show-last-directive)
   (ad-last-directive))

(= (last-directive-admitted)
   (get-state &ad-last-admitted))

(= (send-directive-result $intro)
   (if (== (get-state &ad-last-target) "")
       (send "No directive has been run yet.")
       (if (== (get-state &ad-last-admitted) False)
           (send (py-str ("Directive was not admitted: "
                          (get-state &ad-last-reason))))
           (if (== (get-state &ad-last-code) "")
               (send (py-str ($intro (newline)
                              (get-state &ad-last-output))))
               (send (py-str ($intro (newline)
                              "```python" (newline)
                              (get-state &ad-last-code) (newline)
                              "```" (newline)
                              "Output:" (newline)
                              (get-state &ad-last-output))))))))

(= (ad-init)
   (progn
      (ad-clear-last-directive)
      (log INFO "attention" "Initializing attention directives")
      ATTENTION-DIRECTIVES-INITIALIZED))

(= (initAttentionDirectives)
   (ad-init))
