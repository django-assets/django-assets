"""Trades exception types (trades spec §2.4)."""


class OverAllocationError(ValueError):
    """The allocation would exceed the leg's amount across all trades
    and categories (the ADR-0030 partition rule). App-level pre-check;
    the deferred trade_allocations_within_leg trigger is the backstop.
    """


class UnbalancedVirtualTransferError(ValueError):
    """Per-instrument entry sums of a virtual transfer are non-zero
    (ADR-0031). App-level pre-check; the deferred
    virtual_entries_balanced trigger is the backstop.
    """
