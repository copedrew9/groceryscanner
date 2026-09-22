# My four tests, written by hand once scans.py exists:
#
#   1. Idempotency        - the same nonce posted twice changes inventory once;
#                           the second call returns "duplicate". (criterion 1)
#   2. Quantity flooring  - quantity never falls below zero; a remove at 0
#                           returns "rejected_not_in_stock". (criteria 4, 5)
#   3. Partial batch      - a batch with one invalid element returns "invalid"
#                           for that element and still applies the rest. (criterion 2)
#   4. Event replay       - replay-events rebuilds inventory to a state
#                           identical to live. (criterion 7)
