!(import! &self ./tests/lib/utils)
!(import! &self ../src/helper.py)
!(import! &self ../src/utils)
!(import! &self ../src/context)

!(change-state! &cfv2-current-frame-id FrameHistoryTest)
!(change-state! &cfv2-current-history-summary "")
!(change-state! &cfv2-frame-event-space ())

!(test (== AgentDirective (cfv2-history-category AgentDirective)) True)
!(test (== Result (cfv2-history-category ResultAdded)) True)
!(test (== Milestone (cfv2-history-category OtherHistoryKind)) True)

!(cfv2-record-message-for-current-frame "Check the weather; quote: \"rain\"; path: C:\\\\temp")
!(cfv2-record-command-batch (SearchWeather) (WeatherResult rain))
!(cfv2-record-frame-note ResultAdded (WeatherResult rain))

!(test (not (== () (cfv2-get-frame-events FrameHistoryTest UserDirective))) True)
!(test (not (== () (cfv2-get-frame-events FrameHistoryTest CommandBatch))) True)
!(test (not (== () (cfv2-get-frame-events FrameHistoryTest Result))) True)
!(test (== () (cfv2-get-frame-events OtherFrameHistoryTest ())) True)
!(test (contains-text (get-state &cfv2-current-history-summary) "UserDirective") True)
!(test (contains-text (get-state &cfv2-current-history-summary) "CommandBatch") True)
!(test (contains-text (get-state &cfv2-current-history-summary) "quote: \"rain\"") True)
;; Check both sides of the path without relying on PeTTa's backslash escaping.
!(test (contains-text (get-state &cfv2-current-history-summary) "path: C:") True)
!(test (contains-text (get-state &cfv2-current-history-summary) "temp") True)
!(test (<= (string_length (get-state &cfv2-current-history-summary)) (cfv2HistorySummaryLimit)) True)
