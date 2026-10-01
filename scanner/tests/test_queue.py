# My queue tests, written by hand once queue.py exists:
#
#   1. Overflow drops the OLDEST, not the newest.      (criterion 11)
#   2. release() frees a slot for every result kind,
#      including "duplicate" and "invalid".            (spec 4, client rule 3)
#   3. batch() returns oldest first and does not
#      remove anything until release() is called.
#   4. Five scans during a server outage all survive
#      and all send once it returns.                   (criterion 9)
