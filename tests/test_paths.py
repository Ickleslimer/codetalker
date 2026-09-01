from codetalker.utils.paths import (
    normalize_working_directory,
    working_directories_match,
)


def test_normalize_working_directory_windows_paths():
    assert normalize_working_directory(r"C:\Work\project") == r"C:\Work\project"
    assert normalize_working_directory(r"C:\Work\project/") == r"C:\Work\project"
    assert normalize_working_directory("file:///C:/Work/project") == r"C:\Work\project"


def test_working_directories_match_exact_and_case_insensitive():
    assert working_directories_match(r"C:\Work\project", r"c:\work\project")
    assert working_directories_match(
        r"C:\Work\project\src",
        r"C:\Work\project",
        allow_prefix=True,
    )
    assert not working_directories_match(
        r"C:\Work\project\src",
        r"C:\Work\other",
    )


def test_working_directories_match_rejects_empty():
    assert not working_directories_match(None, r"C:\Work\project")
    assert not working_directories_match(r"C:\Work\project", "")
