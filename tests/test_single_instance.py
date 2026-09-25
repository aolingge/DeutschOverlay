import os
import uuid

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from deutsch_overlay.single_instance import SingleInstance


def test_second_launch_notifies_first_instance():
    application = QApplication.instance() or QApplication([])
    name = f"DeutschOverlay-test-{uuid.uuid4().hex}"
    visits = []
    first = SingleInstance(name, lambda: visits.append("show"))
    second = SingleInstance(name, lambda: visits.append("wrong"))
    try:
        assert first.listen()
        assert not second.listen()
        assert second.notify_existing()
        for _ in range(30):
            application.processEvents()
            if visits:
                break
        assert visits == ["show"]
    finally:
        first.close()
        second.close()
