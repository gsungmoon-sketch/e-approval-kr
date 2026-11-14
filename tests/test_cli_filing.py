"""gyeol CLI 의 문서 분류·보관 명령(dclass / project / folder tag·untag): 왕복·권한 거부·감사로그."""
import contextlib
import io
import json
import unittest

from helpers import rmtree, world

from gyeoljae import cli


class CliFilingTest(unittest.TestCase):
    def setUp(self):
        self.app, self.d = world()
        self.did = self.app.create_draft("dev1", "분류 시험", "본문", ["devlead"], form="general",
                                         fields={"purpose": "p"})["id"]

    def tearDown(self):
        rmtree(self.d)

    def run_cli(self, user, *argv):
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            rc = cli.main(["--data", self.d, "-u", user, "--json", *map(str, argv)])
        return rc, json.loads(f.getvalue())

    def ok(self, user, *argv):
        rc, r = self.run_cli(user, *argv)
        self.assertEqual(rc, 0, r)
        return r

    def refused(self, user, *argv, code="forbidden"):
        rc, r = self.run_cli(user, *argv)
        self.assertEqual(rc, 2, r)
        self.assertEqual(r["error"], code, r)
        return r

    def audit(self, action):
        return [e for e in self.app.audit_list("admin", limit=1000) if e["action"] == action]

    def test_dclass_round_trip(self):
        rows = self.ok("devlead", "dclass", "list")
        self.assertEqual([r["name"] for r in rows], ["회계", "인사", "비품", "일반"])
        r = self.ok("devlead", "dclass", "add", "설계")
        sub = self.ok("devlead", "dclass", "add", "도면", "--parent", r["id"])
        self.assertEqual(sub["parent"], r["id"])
        self.ok("devlead", "dclass", "rename", sub["id"], "설계도면")
        rows = self.ok("devlead", "dclass", "list")
        self.assertIn(("설계도면", 1), [(x["name"], x["depth"]) for x in rows])
        # 기안자(기안 상태)가 분류 지정 → get 으로 확인
        r2 = self.ok("dev1", "dclass", "set", self.did, sub["id"])
        self.assertEqual(r2["dclass"]["id"], sub["id"])
        self.assertEqual(self.ok("dev1", "dclass", "get", self.did)["dclass"]["name"], "설계도면")
        self.assertEqual(self.audit("reclass")[-1]["detail"]["dclass"], ["일반", "설계도면"])  # 양식 기본 분류 → 새 분류
        self.assertEqual(self.ok("dev1", "dclass", "set", self.did, "none")["dclass"], None)
        self.refused("devlead", "dclass", "delete", r["id"], code="invalid")  # 하위 분류 먼저
        self.ok("devlead", "dclass", "delete", sub["id"])
        self.ok("devlead", "dclass", "delete", r["id"])
        for a in ("dclass_add", "dclass_rename", "dclass_delete"):
            self.assertTrue(self.audit(a), a)
        self.assertEqual(self.audit("dclass_rename")[-1]["detail"]["after"], "설계도면")
        self.assertEqual(self.audit("dclass_add")[-1]["actor"], "devlead")

    def test_dclass_refusals(self):
        n = len(self.audit("dclass_add"))
        self.refused("dev1", "dclass", "add", "몰래")                  # 부서장급 아님
        self.refused("sallead", "dclass", "add", "남의부서", "--dept", "DEV")
        self.refused("sal1", "dclass", "list", "--dept", "DEV")
        cid = self.ok("devlead", "dclass", "list")[0]["id"]
        self.refused("sallead", "dclass", "rename", cid, "x")
        self.refused("sallead", "dclass", "delete", cid)
        self.refused("sal1", "dclass", "set", self.did, cid, code="not_found")   # 볼 수 없는 문서 = 없음
        self.refused("sal1", "dclass", "get", self.did, code="not_found")
        self.refused("dev1", "dclass", "set", self.did, "abc", code="invalid")
        sal_cls = self.ok("sallead", "dclass", "list")[0]["id"]
        self.refused("dev1", "dclass", "set", self.did, sal_cls, code="invalid")  # 다른 부서 분류
        self.assertEqual(len(self.audit("dclass_add")), n)
        # 상신 뒤에는 기안자도 못 바꿈(부서장급은 가능)
        self.app.submit("dev1", self.did)
        self.refused("dev1", "dclass", "set", self.did, cid)
        self.ok("devlead", "dclass", "set", self.did, cid)

    def test_project_round_trip_and_tags(self):
        p = self.ok("devlead", "project", "add", "신제품", "--code", "NP-1", "--depts", "DEV", "--users", "sal1")
        self.assertEqual((p["depts"], p["users"], p["status"]), (["DEV"], ["sal1"], "open"))
        self.assertEqual(self.audit("project_create")[-1]["detail"]["name"], "신제품")
        self.assertIn(p["id"], [x["id"] for x in self.ok("dev1", "project", "list")])
        self.assertNotIn(p["id"], [x["id"] for x in self.ok("hr", "project", "list")])
        r = self.ok("dev1", "project", "tag", self.did, p["id"])
        self.assertEqual(r["projects"], [p["id"]])
        self.assertEqual(self.audit("reclass")[-1]["detail"]["projects"], [[], ["신제품"]])
        self.assertEqual([d["id"] for d in self.ok("dev1", "project", "show", p["id"])["docs"]], [self.did])
        self.assertEqual(self.ok("dev1", "project", "tag", self.did, p["id"])["projects"], [p["id"]])  # 두 번 = 그대로
        self.assertEqual(self.ok("dev1", "project", "untag", self.did, p["id"])["projects"], [])
        self.ok("devlead", "project", "members", p["id"], "--users", "sal1,hr")
        p2 = self.ok("devlead", "project", "edit", p["id"], "--name", "신제품2", "--code", "")
        self.assertEqual((p2["name"], p2["code"], p2["depts"], p2["users"]), ("신제품2", None, ["DEV"], ["hr", "sal1"]))
        self.ok("devlead", "project", "members", p["id"], "--depts", "")
        self.assertEqual(self.ok("devlead", "project", "status", p["id"], "closed")["status"], "closed")
        ch = self.audit("project_update")[-1]["detail"]["changes"]
        self.assertEqual(ch["status"], ["open", "closed"])
        self.assertEqual(self.ok("devlead", "project", "list")[0]["depts"], [])

    def test_project_refusals(self):
        self.refused("dev1", "project", "add", "몰래")                      # 부서장급·관리자만
        p = self.ok("devlead", "project", "add", "A", "--depts", "DEV")
        self.refused("sallead", "project", "members", p["id"], "--users", "sal1")  # 만든 사람·관리자만
        self.refused("sallead", "project", "status", p["id"], "closed")
        self.refused("sal1", "project", "show", p["id"], code="not_found")       # 참여 안 함 = 없음
        self.refused("devlead", "project", "add", "B", "--users", "nobody", code="invalid")
        # 참여하지 않은 사업에는 태그 불가, 종료 사업도 불가
        q = self.ok("sallead", "project", "add", "영업만", "--depts", "SAL")
        self.refused("dev1", "project", "tag", self.did, q["id"])
        self.ok("devlead", "project", "status", p["id"], "closed")
        self.refused("dev1", "project", "tag", self.did, p["id"], code="invalid")
        self.refused("sal1", "project", "tag", self.did, q["id"], code="not_found")  # 볼 수 없는 문서
        self.assertEqual(self.audit("reclass"), [])

    def test_folder_tag_untag(self):
        f1 = self.ok("dev1", "folder", "create", "보관1")
        f2 = self.ok("dev1", "folder", "create", "보관2")
        self.assertEqual(self.ok("dev1", "folder", "tag", self.did, f1["id"])["folders"], [f1["id"]])
        self.assertEqual(sorted(self.ok("dev1", "folder", "tag", self.did, f2["id"])["folders"]),
                         sorted([f1["id"], f2["id"]]))
        self.assertEqual(self.ok("dev1", "folder", "untag", self.did, f1["id"])["folders"], [f2["id"]])
        self.assertEqual([d["id"] for d in self.app.folder_docs("dev1", f2["id"])["docs"]], [self.did])
        g = self.ok("hr", "folder", "create", "남의 폴더")
        self.refused("dev1", "folder", "tag", self.did, g["id"], code="not_found")   # 남의 폴더
        self.refused("hr", "folder", "tag", self.did, g["id"], code="not_found")     # 볼 수 없는 문서
        self.assertEqual(self.app.folder_docs("hr", g["id"])["docs"], [])

    def test_help_lists_commands(self):
        f = io.StringIO()
        with contextlib.redirect_stdout(f), self.assertRaises(SystemExit):
            cli.main(["project", "--help"])
        self.assertIn("{list,add,show,edit,members,status,tag,untag}", f.getvalue())


if __name__ == "__main__":
    unittest.main()
