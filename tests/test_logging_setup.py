from pathlib import Path

from app.core.logging import setup_logging


def test_setup_logging_writes_to_dedicated_file_with_overwrite_mode(monkeypatch, tmp_path: Path) -> None:
    events: list[tuple[object, dict]] = []

    class Settings:
        log_level = "INFO"
        log_json = True
        log_file = str(tmp_path / "logs" / "yra.log")
        log_rotation = "10 MB"
        log_retention = "10 days"

    def fake_remove() -> None:
        events.append(("remove", {}))

    def fake_add(sink, **kwargs):
        events.append((sink, kwargs))
        return len(events)

    monkeypatch.setattr("app.core.logging.get_settings", lambda: Settings())
    monkeypatch.setattr("app.core.logging.logger.remove", fake_remove)
    monkeypatch.setattr("app.core.logging.logger.add", fake_add)

    setup_logging()

    assert events[0] == ("remove", {})
    file_sink, file_kwargs = events[2]
    assert file_sink == Path(Settings.log_file)
    assert file_kwargs["mode"] == "w"
    assert Path(Settings.log_file).parent.exists()
