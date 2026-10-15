from ctxstudy.score import parse_junit

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="4">
  <testcase classname="pkg.tests.test_a.TestA" name="test_ok" time="0.01"/>
  <testcase classname="pkg.tests.test_a.TestA" name="test_bad" time="0.01"><failure message="boom"/></testcase>
  <testcase classname="pkg.tests.test_a" name="test_param[1-2]" time="0.01"><skipped message="no numpy"/></testcase>
  <testcase classname="" name="pkg.tests.test_b" time="0"><error message="collection failure"/></testcase>
</testsuite></testsuites>"""


def test_parse_junit_outcomes():
    r = parse_junit(JUNIT)
    assert r["pkg.tests.test_a.TestA::test_ok"] == "passed"
    assert r["pkg.tests.test_a.TestA::test_bad"] == "failed"
    assert r["pkg.tests.test_a::test_param[1-2]"] == "skipped"
    assert r["::pkg.tests.test_b"] == "failed"


def test_parse_junit_empty():
    assert parse_junit("") == {}
