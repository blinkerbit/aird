from scripts.release_version import next_version


def test_main_bumps_patch():
    assert next_version("0.5.6", "main", 0) == "0.5.7"


def test_main_promotes_prerelease():
    assert next_version("0.5.7rc2", "main", 0) == "0.5.7"
    assert next_version("0.5.7.dev4", "main", 0) == "0.5.7"


def test_rc_and_dev_share_the_next_patch():
    assert next_version("0.5.6", "rc", 1) == "0.5.7rc1"
    assert next_version("0.5.7rc2", "rc", 3) == "0.5.7rc3"
    assert next_version("0.5.6", "dev", 99) == "0.5.7.dev99"
    assert next_version("0.5.7.dev5", "dev", 8) == "0.5.7.dev8"
