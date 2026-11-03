"""Shared contract suites and fakes. Shipped in the package so services can reuse them.

A contract suite is a plain class with `test_*` methods and a `make_*` hook. An adapter's
test module subclasses it (named `Test...`) and implements the hook.
"""
