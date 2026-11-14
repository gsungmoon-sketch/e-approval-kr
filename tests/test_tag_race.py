"""CLI 태그 넣기/빼기(project/folder tag·untag)가 동시에 돌아도 서로의 변경을 덮어쓰지 않는지.
예전 구현은 '현재 목록 읽기'와 '전체 교체 쓰기'가 다른 트랜잭션이라 나중 쓰기가 앞 변경을 지웠다."""
import threading
import unittest

from helpers import rmtree, world

from gyeoljae.cli_filing import folder_tag, project_tag
from gyeoljae.core import App

N = 8


class TagRaceTest(unittest.TestCase):
    def setUp(self):
        self.app, self.d = world()
        self.did = self.app.create_draft("dev1", "경합 시험", "본문", ["devlead"], form="general",
                                         fields={"purpose": "p"})["id"]

    def tearDown(self):
        rmtree(self.d)

    def hammer(self, fn, ids, on):
        bar = threading.Barrier(len(ids))
        errs = []

        def work(i):
            try:
                a = App(self.d)  # 프로세스마다 따로 연 것처럼 인스턴스도 따로
                bar.wait()
                fn(a, "dev1", self.did, i, on=on)
            except Exception as e:  # pragma: no cover
                errs.append(e)
        ts = [threading.Thread(target=work, args=(i,)) for i in ids]
        for t in ts:
            t.start()
        for t in ts:
            t.join(60)
        self.assertEqual(errs, [])

    def test_concurrent_project_tags(self):
        pids = [self.app.project_create("devlead", f"사업{i}", depts=["DEV"])["id"] for i in range(N)]
        for _ in range(3):
            self.hammer(project_tag, pids, True)
            self.assertEqual(sorted(p["id"] for p in self.app.doc_tags("dev1", self.did)["projects"]), sorted(pids))
            self.hammer(project_tag, pids[::2], False)
            self.assertEqual(sorted(p["id"] for p in self.app.doc_tags("dev1", self.did)["projects"]),
                             sorted(pids[1::2]))
            self.hammer(project_tag, pids[::2], True)

    def test_concurrent_folder_tags(self):
        fids = [self.app.folder_create("dev1", f"폴더{i}")["id"] for i in range(N)]
        for _ in range(3):
            self.hammer(folder_tag, fids, True)
            self.assertEqual(sorted(f["id"] for f in self.app.doc_tags("dev1", self.did)["folders"]), sorted(fids))
            self.hammer(folder_tag, fids[::2], False)
            self.assertEqual(sorted(f["id"] for f in self.app.doc_tags("dev1", self.did)["folders"]),
                             sorted(fids[1::2]))
            self.hammer(folder_tag, fids[::2], True)

    def test_single_toggle_semantics(self):
        p = self.app.project_create("devlead", "하나", depts=["DEV"])["id"]
        self.assertEqual(project_tag(self.app, "dev1", self.did, p)["projects"], [p])
        self.assertEqual(project_tag(self.app, "dev1", self.did, p)["changed"], {})  # 두 번 = 그대로
        self.assertEqual(project_tag(self.app, "dev1", self.did, p, on=False)["projects"], [])


if __name__ == "__main__":
    unittest.main()
