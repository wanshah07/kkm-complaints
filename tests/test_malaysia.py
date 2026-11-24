"""Malaysia filter for hashtag results (24 Nov 2026): captions taken from the 46 posts the first hashtag run filed."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper"))

from malaysia import classify, keep  # noqa: E402


class Classify(unittest.TestCase):
    def v(self, text):
        return classify(text)[0]

    def test_malaysian_signals(self):
        self.assertEqual(self.v("Found my new favourite tinted serum sunscreen! @skintific.my"), "my")
        self.assertEqual(self.v("No-needle PDRN glow at home. #SkintificMY #SkintificPDRN"), "my")
        self.assertEqual(self.v("Pernah rasa kurang selesa apabila wajah berminyak selepas beberapa jam?"), "my")
        self.assertEqual(self.v("Nak kulit nampak plump, makin anjal & glowing?"), "my")
        self.assertEqual(self.v("Skintific PDRN serum RM59 only, COD Selangor"), "my")

    def test_other_countries(self):
        self.assertEqual(self.v("Moisturizer gemoy yang bikin auto glowing, aku udah coba banget, kamu udah?"), "not_my")
        self.assertEqual(self.v("Skintific Foundation Stick Rp129.000 ready stock, gratis ongkir"), "not_my")
        self.assertEqual(self.v("ทุกวันนี้ผิวดีขึ้นมากๆ #SkintificTH"), "not_my")
        self.assertEqual(self.v("skin looking expensive #SKINTIFICPH #SKINTIFICGlow"), "not_my")
        self.assertEqual(self.v("Skintific event in Surabaya next week"), "not_my")

    def test_unclear(self):
        self.assertEqual(self.v("Harsh truth about PDRN skincare: not every formula is the same."), "unclear")
        self.assertEqual(self.v(""), "unclear")

    def test_indonesian_and_malaysian_malay_share_words(self):
        # yang / dan / untuk / kulit alone prove nothing
        self.assertEqual(self.v("Skincare yang sesuai untuk kulit sensitif dan berminyak"), "unclear")

    def test_a_malaysian_tag_beats_one_indonesian_word(self):
        self.assertEqual(self.v("Review jujur #skintificmy #skintificmalaysia kulit sensitif, aku suka"), "my")


class Keep(unittest.TestCase):
    def test_modes(self):
        self.assertTrue(keep("my", "strict"))
        self.assertFalse(keep("unclear", "strict"))
        self.assertFalse(keep("not_my", "strict"))
        self.assertTrue(keep("unclear", "loose"))
        self.assertFalse(keep("not_my", "loose"))
        self.assertTrue(keep("not_my", ""))      # no filter: everything is kept


class Wiring(unittest.TestCase):
    def test_workflow_and_main(self):
        wf = (ROOT / ".github" / "workflows" / "scraper.yml").read_text(encoding="utf-8")
        self.assertIn('--malaysia "$IN_MALAYSIA"', wf)
        self.assertNotIn("schedule", [l for l in wf.splitlines() if "IN_MALAYSIA:" in l][0])
        main = (ROOT / "scraper" / "main.py").read_text(encoding="utf-8")
        self.assertIn("malaysia=args.malaysia, keep_owners=ht_keep_owners", main)
        hashtag = (ROOT / "scraper" / "ig_hashtag.py").read_text(encoding="utf-8")
        self.assertIn("owner not in self.keep_owners", hashtag)


if __name__ == "__main__":
    unittest.main()
