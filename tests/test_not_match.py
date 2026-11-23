"""tools/not_match.py: confident matches only. A wrong NOT number is worse than a blank one."""
import unittest
from tools import not_match as nm

PRODUCTS = [
    ("NOT230805974K", "Eucerin Spotless Brightening Booster Serum", "BEIERSDORF (MALAYSIA) SDN BHD"),
    ("NOT210106153K", "URIAGE Eau Thermale", "BETIME MARKETING SDN. BHD"),
    ("NOT260204191K", "Cerave Invisible Hydrating Sunscreen For Body And Face SPF 50+", "LOREAL MALAYSIA SDN. BHD."),
    ("NOT250701094K", "Nutox Lifte Youth Micro Active Serum", "TOHTONKU SDN. BHD."),
    ("NOT990101111K", "Cream", "SOME SDN BHD"),
    ("NOT260606001K", "Eucerin Spotless Brightening Lotion", "BEIERSDORF (MALAYSIA) SDN BHD"),
    ("NOT250301001K", "ABC Matte Lipstick Rose (A01)", "ABC SDN BHD"),
    ("NOT250301002K", "ABC Matte Lipstick Rose (A02)", "ABC SDN BHD"),
    ("NOT260101001K", "Eucerin Eye Cream Special", "BEIERSDORF (MALAYSIA) SDN BHD"),
    ("NOT210404318K", "Uriage Bariederm Cica-Daily Serum", "BETIME MARKETING SDN. BHD"),
    ("NOT250101001K", "Eucerin Sun Serum Oil Control Fluid SPF50+ PA++++", "BEIERSDORF (MALAYSIA) SDN BHD"),
    ("NOT250101002K", "Eucerin Sun Serum Oil Control Fluid SPF30 PA+++", "BEIERSDORF (MALAYSIA) SDN BHD"),
]
ENTRIES, IDX = nm.build_index(PRODUCTS)


def run(L, brand="Eucerin", date="2026-10-01", plat="Instagram"):
    return nm.match_one({"L": L, "brand": brand, "date": date, "plat": plat}, ENTRIES, IDX)


class NotMatch(unittest.TestCase):
    def test_exact_with_accent_and_case(self):
        r = run("Uriage Eau Thermale", "Uriage")
        self.assertEqual((r["tier"], r["no"]), ("exact", "NOT210106153K"))
        r = run("Nutox LIFTÉ YOUTH Micro Active Serum", "Nutox")
        self.assertEqual((r["tier"], r["no"]), ("exact", "NOT250701094K"))

    def test_a_form_word_that_differs_is_not_a_match(self):
        # the lotion in the complaint must not take the serum's number
        r = run("Eucerin Spotless Brightening Lotion")
        self.assertEqual(r["no"], "NOT260606001K")
        r = run("Eucerin Spotless Brightening Cream")
        self.assertIsNone(r["tier"])

    def test_category_range_or_several_products_never_match(self):
        for L in ("Eucerin Spotless range", "eye cream (category referenced; no specific brand)",
                  "Eucerin Spotless Brightening Booster Serum; Eucerin Eye Cream Special", "Eucerin"):
            self.assertIsNone(run(L)["tier"], L)

    def test_a_doctor_account_needs_an_exact_name(self):
        r = run("Cerave Invisible Hydrating Sunscreen SPF 50+", "dr_ingky")
        self.assertIsNone(r["tier"])           # contained but not exact
        r = run("Eucerin Spotless Brightening Booster Serum", "dr_ingky")
        self.assertEqual(r["tier"], "exact")

    def test_a_generic_entry_is_not_taken_for_a_short_name(self):
        self.assertIsNone(run("Cream", "dr_ingky")["tier"])

    def test_one_product_under_several_numbers_is_held(self):
        r = run("ABC Matte Lipstick Rose", "ABC")
        self.assertEqual(r["tier"], "hold")
        self.assertIn("several numbers", r["why"])

    def test_notified_after_the_post_is_held(self):
        r = run("Eucerin Spotless Brightening Booster Serum", date="2023-01-10")
        self.assertEqual(r["tier"], "hold")
        self.assertIn("after the post", r["why"])
        r = run("Eucerin Spotless Brightening Booster Serum", date="2023-08-20")
        self.assertEqual(r["tier"], "exact")

    def test_brand_gate_other_brands_same_words(self):
        # "Cica Daily Serum" from Cetaphil must not take Uriage's entry
        r = run("Cica Daily Serum", "Cetaphil")
        self.assertIsNone(r["tier"])

    def test_a_different_spf_is_a_different_product(self):
        # long names share more than 80% of their words; the SPF number still decides
        r = run("Eucerin Sun Serum Oil Control Fluid SPF50+ PA++++ 50ml")
        self.assertEqual(r["no"], "NOT250101001K")
        r = run("Eucerin Sun Serum Oil Control Fluid SPF40 PA++++")
        self.assertIsNone(r["tier"])

    def test_pack_size_is_not_a_difference(self):
        r = run("Eucerin Sun Serum Oil Control Fluid SPF30 PA+++ 100ml")
        self.assertEqual(r["no"], "NOT250101002K")

    def test_not_month(self):
        self.assertEqual(nm.not_month("NOT230805974K"), (2023, 8))
        self.assertIsNone(nm.not_month("tiada"))


if __name__ == "__main__":
    unittest.main()
