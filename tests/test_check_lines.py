from scripts.check_lines import too_long


def test_no_python_or_html_file_is_longer_than_300_lines():
    assert too_long() == []


def test_the_check_catches_a_long_file(tmp_path):
    (tmp_path / "long.py").write_text("x = 1\n" * 301)
    (tmp_path / "short.html").write_text("<p></p>\n")
    assert [str(p) for p, _ in too_long(tmp_path)] == ["long.py"]
