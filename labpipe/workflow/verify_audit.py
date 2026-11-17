"""Verification entry point: original regression plus public audit tally.

Run from the repository root; it chains the existing unittest discovery with the
public hand-tally checks in ``audit_cases`` (including a real evidence write
abort) and exits nonzero on the first failure.
"""
import sys
import unittest


def main():
    suite = unittest.defaultTestLoader.discover('tests', top_level_dir='.')
    outcome = unittest.TextTestRunner(verbosity=2).run(suite)
    if not outcome.wasSuccessful():
        return 1
    import audit_cases
    audit_cases.v1_check()
    audit_cases.audited_check('lp_result_audit')
    return 0


if __name__ == '__main__':
    sys.exit(main())
