from pathlib import Path

from app.application_tracker.browser.profiles import profile_directory


def test_profile_directory_is_stable_per_user_and_hostname(tmp_path: Path) -> None:
    first = profile_directory(
        tmp_path,
        user_id="student@example.com",
        url="https://jobs.example.com/applications/1",
    )
    second = profile_directory(
        tmp_path,
        user_id="student@example.com",
        url="https://jobs.example.com/applications/2",
    )

    assert first == second
    assert first.is_relative_to(tmp_path)
    assert "student@example.com" not in str(first)
    assert "jobs.example.com" not in str(first)


def test_profile_directory_separates_users_and_sites(tmp_path: Path) -> None:
    first_user = profile_directory(
        tmp_path,
        user_id="first-user",
        url="https://jobs.example.com/applications/1",
    )
    second_user = profile_directory(
        tmp_path,
        user_id="second-user",
        url="https://jobs.example.com/applications/1",
    )
    second_site = profile_directory(
        tmp_path,
        user_id="first-user",
        url="https://careers.example.net/applications/1",
    )

    assert len({first_user, second_user, second_site}) == 3
