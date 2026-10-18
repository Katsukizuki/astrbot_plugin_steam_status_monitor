import unittest
from datetime import datetime

from src.application.services.ranking import RankingService


class _RankingHost:
    """最小插件宿主：只提供 RankingService 需要的账本与脏标志。"""

    def __init__(self):
        self.play_records = {}
        self.session_records = {}
        self._session_dirty = False
        self._recorded_quit_cache = {}


class SessionRecordTests(unittest.TestCase):
    def _service(self, host):
        return RankingService(host, now=lambda: datetime(2026, 8, 28, 12, 0))

    def test_same_session_is_not_duplicated_across_groups(self):
        host = _RankingHost()
        ranking = self._service(host)

        ranking.record_session("sid", "730", "Game", 1000, 1123, 2.05, "group-a")
        ranking.record_session("sid", "730", "Game", 1000, 1123, 2.05, "group-b")

        sessions = host.session_records["sid"]
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["duration_min"], 2.05)
        self.assertEqual(sessions[0]["group_id"], "group-a")


if __name__ == "__main__":
    unittest.main()
