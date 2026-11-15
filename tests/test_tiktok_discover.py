import json, unittest
from tests import _paths  # noqa: F401
import tiktok_discover as td


def embed(handle, vids, private=False, error=False):
    node = {"isError": error, "userInfo": {"uniqueId": handle, "privateAccount": private}, "videoList": vids}
    return '<html><script id="__FRONTITY_CONNECT_STATE__" type="application/json">' + json.dumps(
        {"source": {"data": {"/embed/@" + handle: node, "strategy": {}}}}) + "</script></html>"


def vid(i, author="ceravemy", private=False):
    return {"id": str(i), "authorUniqueId": author, "privateItem": private, "desc": "caption %s" % i}


NEW = 7707596677791747346          # 13 Nov 2026
OLD = 7300000000000000000          # 2023


class Discover(unittest.TestCase):
    def test_handles(self):
        self.assertEqual(td.clean_handle("@ceravemy"), "ceravemy")
        self.assertEqual(td.clean_handle("https://www.tiktok.com/@o.two.o_my?lang=en"), "o.two.o_my")
        self.assertEqual(td.clean_handle("not a handle!"), "")
        self.assertEqual(td.clean_handle("a\nb=c"), "")

    def test_targets(self):
        rows = [{"name": "CeraVe", "active": True, "handles": {"Instagram": "cerave", "TikTok": "ceravemy"}, "product_hints": ["SA"], "type": ""},
                {"name": "Off", "active": False, "handles": {"TikTok": "off"}},
                {"name": "Valorith", "active": True, "handles": {"TikTok": "valorith.my"}},
                {"name": "Dup", "active": True, "handles": {"TikTok": "@CeraveMY"}},
                {"name": "Bad", "active": True, "handles": {"TikTok": "see bio"}},
                {"name": "Dr X", "active": True, "handles": {"tiktok": "drx"}, "type": "Doctor"}]
        t, notes = td.tiktok_targets(rows)
        self.assertEqual(sorted(t), ["ceravemy", "drx"])
        self.assertEqual(t["drx"]["type"], "Doctor")
        self.assertEqual(len(notes), 3)                      # own brand, duplicate handle, not a handle

    def test_embed(self):
        html = embed("ceravemy", [vid(NEW), vid(NEW), vid(NEW + 1, private=True), vid(NEW + 2, author="other"), {"id": "abc"}])
        v, problem = td.parse_embed(html, "CeraveMY")
        self.assertEqual((problem, [x["id"] for x in v]), ("", [str(NEW)]))
        self.assertIn("no page state", td.parse_embed("<html>challenge</html>", "x")[1])
        self.assertIn("private", td.parse_embed(embed("a", [vid(NEW, "a")], private=True), "a")[1])
        self.assertIn("error", td.parse_embed(embed("a", [], error=True), "a")[1])

    def test_choose(self):
        found = {"ceravemy": [{"id": str(NEW + i), "desc": ""} for i in range(5)] + [{"id": str(OLD), "desc": ""}]}
        links, c = td.choose(found, {str(NEW)}, "2024-06-01", 3)
        self.assertEqual((c["listed"], c["known"], c["old"], c["new"]), (6, 1, 1, 4))
        self.assertEqual(len(links), 3)
        self.assertTrue(links[0].endswith("/video/%d" % (NEW + 4)))            # newest first
        self.assertEqual(td.id_date(str(NEW)), "2026-11-13")
        self.assertRegex(td.min_post_date(), r"^\d{4}-\d{2}-\d{2}$")


if __name__ == "__main__":
    unittest.main()
