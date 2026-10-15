from ctxstudy.config import RepoConfig
from ctxstudy.mine import classify, clean_markdown, glob_to_regex, prompt_from_commit

CFG = RepoConfig(
    name="x", url="https://github.com/o/x",
    test_globs=["**/tests/**", "**/test_*.py", "**/*_test.py", "**/conftest.py"],
    src_globs=["**/*.py"],
    exclude_globs=["doc/**", "docs/**", "examples/**"],
)


def test_double_star_matches_any_depth_including_root():
    rx = glob_to_regex("**/tests/**")
    assert rx.match("tests/test_a.py")
    assert rx.match("pkg/sub/tests/test_a.py")
    assert not rx.match("pkg/testsuite.py")


def test_single_star_stays_in_one_segment():
    assert glob_to_regex("docs/*.py").match("docs/a.py")
    assert not glob_to_regex("docs/*.py").match("docs/sub/a.py")


def test_classify():
    assert classify("networkx/algorithms/tests/test_cycles.py", CFG) == "test"
    assert classify("networkx/conftest.py", CFG) == "test"
    assert classify("networkx/algorithms/cycles.py", CFG) == "src"
    assert classify("doc/conf.py", CFG) == "other"
    assert classify("README.rst", CFG) == "other"


def test_prompt_from_commit_drops_squash_bullets_and_pr_number():
    subject = "BUG: fix vf2 with isolated nodes (#8895)"
    body = "* add failing test\n\n* fix it\n\nThe matcher skipped isolated nodes.\n\nCo-authored-by: A <a@b.c>"
    prompt = prompt_from_commit(subject, body)
    assert prompt.startswith("BUG: fix vf2 with isolated nodes")
    assert "(#8895)" not in prompt
    assert "* add failing test" not in prompt
    assert "The matcher skipped isolated nodes." in prompt
    assert "Co-authored-by" not in prompt


def test_clean_markdown_removes_template_noise():
    text = "Title\n\n<!-- template hint -->\n- [x] I added tests\nReal description."
    assert clean_markdown(text) == "Title\n\nReal description."
