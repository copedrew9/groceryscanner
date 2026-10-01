"""The loop, and wiring the modules together. Spec 5.1.
Written by hand; do not edit.

Single-threaded. No threads, no locks, no shared state.

    init: load config, open serial, set up GPIO, create Http

    loop:
      timeout = backoff.ms_until_due(hw.now_ms()) when anything is queued,
                else IDLE_TIMEOUT_MS
      selectors.select(timeout)
      if readable:  hw.serial_read -> framer.feed -> for each barcode:
                        action = REMOVE if hw.switch_is_remove() else ADD
                            (spec 5.4: read the switch HERE, at completion,
                             not at send time -- criterion 8)
                        queue.add(ScanRecord(hw.new_nonce(), barcode, action,
                                             hw.now_ms()))
                        dropped -> log ERROR + hw.signal(OVERFLOW)
                        backoff.on_new_scan(hw.now_ms())
      if hangup:    hw.serial_close, reopen with backoff (criterion 12)
      if due:       proto.build_request(queue.batch())
                    hw.Http.post_scans
                    status 200 -> proto.parse_results
                                  None means no usable answer: free nothing
                                  else release each nonce, signal per result,
                                  backoff.on_success
                    status 401 -> permanent. hw.signal(AUTH_FAILED), log,
                                  stop sending until restarted (spec 4, rule 4)
                    anything else or None -> backoff.on_failure
"""

from __future__ import annotations


def main() -> int:
    raise NotImplementedError


if __name__ == "__main__":
    raise SystemExit(main())
