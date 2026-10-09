/**
 * KKM Cosmetic Complaint System — Google Apps Script backend
 * ==========================================================
 * Database : the bound Google Sheet (tab "Complaints"), plus a "Lookups" tab for
 *            the expandable Brand / Platform / Violation-type lists.
 * Webhook  : doPost(e) — JSON from the Python scraper (or any client) with a
 *            shared-secret token. Actions: insert | known_urls | update_status.
 * Web app  : doGet(e)  — serves the dashboard (Index.html) or, with ?action=…,
 *            a JSON API (list | get | known_urls | stats). The standalone dashboard in
 *            web/ (hosted on your own domain) talks to doPost with the DASHBOARD_KEY.
 * UI RPC   : the dashboard calls api* functions through google.script.run.
 * Routes   : ROUTES below is the catalogue of where a complaint goes (NPRA, BPF, MDA,
 *            CKAPS, BKKM, JAKIM/KPDN, MCMC, …; ASA dropped 9 Oct 2026 on Wan's word: "irrelevant for Malaysia"), with the cases that belong to each,
 *            the instrument cited, the reference number asked for and the agency's
 *            complaint channel. The dashboard reads it from `bootstrap`, so a route
 *            is edited HERE and nowhere else. A per-route form template (a Google
 *            pre-fill link, like the KKM one) is kept in Script Properties ROUTE_FORMS.
 * Exports  : `export` returns full rows for CSV; `export_doc` builds a Google Doc
 *            (register of the filtered rows, or one complaint's dossier) and can hand
 *            it back as PDF or Word. Needs the documents scope in appsscript.json.
 *
 * One-time setup (from the Apps Script editor):
 *   1. Run  setup()  once. It builds both tabs, the data validation, the Drive
 *      folder for screenshots, and generates API_TOKEN + DASHBOARD_KEY in
 *      Script Properties. Read them from  Project Settings → Script properties.
 *   2. Deploy → New deployment → Web app → Execute as: Me, Access: Anyone.
 *   3. Give the scraper the /exec URL + API_TOKEN. Open the dashboard at
 *      <exec URL>?key=<DASHBOARD_KEY>  (or without the key when you are logged in
 *      as OWNER_EMAIL — see Script Properties).
 */

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------
var SHEET_NAME = 'Complaints';
var LOOKUP_SHEET_NAME = 'Lookups';

// Column order is the contract with the scraper and the UI. Do not reorder
// without bumping SCHEMA_VERSION and running migrateSchema().
var HEADERS = [
  'ID',                 // 0  auto  KKM-YYYYMMDD-xxxx
  'Date',               // 1  detection date (yyyy-MM-dd)
  'Brand',              // 2
  'Platform',           // 3  Instagram | Facebook | Threads | …
  'Post URL',           // 4  unique key for dedupe
  'Screenshot Link',    // 5  Drive "anyone with link" viewer URL
  'Extracted Text',     // 6
  'Violation Type',     // 7  Medicinal claim | Mechanism claim | … (see Lookups)
  'Violation Reason',   // 8  free text with the Annex I Part 8 / Part 10 citation
  'Status',             // 9  New | In-Progress | Complete | Dismissed
  'Remarks',            // 10
  'Nama Kosmetik',      // 11 product name as advertised (KKM form: Nama kosmetik)
  'Nombor Notifikasi',  // 12 reference number: NOT for a cosmetic, the route's own number otherwise
  'Jenis Aduan',        // 13 the route: a value of ROUTES (JENIS_ADUAN)
  'Deskripsi Aduan',    // 14 ready-to-paste complaint description (BM)
  'Tarikh Melapor',     // 15 date the report was submitted to the agency
  'KKM Feedback',       // 16 the agency's reply pasted in by Wan after submission
  'Source',             // 17 scraper | manual
  'Confidence',         // 18 0–1 from the LLM reviewer, blank for manual
  'Created At',         // 19 ISO timestamp
  'Updated At'          // 20 ISO timestamp
];
var COL = {};
HEADERS.forEach(function (h, i) { COL[h] = i; });

var STATUSES = ['New', 'In-Progress', 'Complete', 'Dismissed'];
var STATUS_FLOW = { 'New': 'In-Progress', 'In-Progress': 'Complete', 'Complete': 'Complete', 'Dismissed': 'Dismissed' };

// Archived is not a Status. A complaint is archived when KKM has replied - that is, when
// 'KKM Feedback' carries anything - because at that point the file is closed and it should
// stop competing for attention with the ones still waiting. Keeping it out of STATUSES means
// no schema change, no migration, and 'Complete' keeps its own meaning: submitted to KKM,
// reply not yet in. Clearing the feedback cell brings the row straight back into the list.
var ARCHIVED_FILTER = 'Archived';

// The list payload used to carry every field of every row, including the two that dominate
// it: 'Extracted Text' (whole captions, routinely 500-2000 characters) and 'Violation Reason'
// (the reviewer's full reasoning, often past 1000). Neither is read in full by the table - the
// reason renders two clamped lines and the caption is not shown at all - so the boot response
// was mostly bytes nobody looked at, growing linearly with the complaint log.
//
// They are truncated rather than dropped so that searching still finds things: Instagram
// captions front-load the product name and the claim. A match deeper than this is caught by
// the server-side search below, so nothing the search box promises is quietly lost.
var LIST_FIELD_LIMIT = 300;
var LIST_TRUNCATED_FIELDS = ['Extracted Text', 'Violation Reason'];

function listRowToObject_(row) {
  var o = rowToObject_(row);
  LIST_TRUNCATED_FIELDS.forEach(function (h) {
    var v = String(o[h] || '');
    if (v.length > LIST_FIELD_LIMIT) { o[h] = v.slice(0, LIST_FIELD_LIMIT); o._truncated = true; }
  });
  return o;
}

function isArchived_(rowVals) {
  return String(rowVals[COL['KKM Feedback']] || '').trim() !== '';
}

// ---------------------------------------------------------------------------
// ROUTES — where a complaint goes, and everything a form for that route will need
// (Wan, 3 Oct 2026: "for routing other than iklan kosmetik, make another options like MDA,
// medical practice etc, so later I can setup to link with relevant complaint form";
// 9 Oct 2026: "Add routes and all the cases related so later easy for me to setup the form").
//
// One entry per regulator route. `value` is what the sheet's Jenis Aduan column stores and
// must never be renamed once rows carry it. `kkm: true` marks the two options of the KKM
// cosmetic Google Form, which keep the automatic pre-fill. Every other route carries:
//   agency   who receives it (BM, as the agency names itself)
//   act      the instrument the complaint rests on; cited in the Deskripsi (BM)
//   cases    what belongs on this route (UI, English) — the triage list
//   ref      the reference number this agency asks for; it lives in the Nombor Notifikasi
//            column with this label instead of "NOT"
//   fields   the sheet columns the agency's form will need, in the order the form asks
//   channel  the agency's own complaint channel: url, how it was checked and when. A url
//            that did not answer at check time is still recorded, marked so.
//   basis    the one BM sentence the Deskripsi Aduan uses for the legal basis
// A pre-fill template for a route (a Google Form link with {Column} placeholders, built the
// way kkm-form-helper.html builds the KKM one) is stored per route in ROUTE_FORMS and wins
// over `channel.url` in the drawer. Channel facts were read live on 9 Oct 2026; `checked`
// says what answered. Re-check before trusting a number — portals move.
// ---------------------------------------------------------------------------
var ROUTES = [
  {
    value: 'Iklan Kosmetik', kkm: true, group: 'KKM',
    agency: 'NPRA — Pusat Pematuhan dan Kawalan Kualiti, Seksyen Surveilan dan Aduan (Bahagian Regulatori Farmasi Negara, KKM)',
    act: 'Guidelines for Control of Cosmetic Products in Malaysia, Annex I Part 8 (Guideline for Cosmetic Claims) dan Part 10 (Guideline for Cosmetic Advertisement); Peraturan-Peraturan Kawalan Dadah dan Kosmetik 1984',
    basis: 'Dakwaan ini tidak dibenarkan untuk produk kosmetik mengikut Guidelines for Control of Cosmetic Products in Malaysia, Annex I Part 8 (Guideline for Cosmetic Claims) dan Part 10 (Guideline for Cosmetic Advertisement), NPRA.',
    ref: { label: 'Nombor Notifikasi (NOT)', hint: 'NOTxxxxxxxxK — verify on QUEST3+', pattern: '^NOT\\s*\\d' },
    cases: ['Medicinal or disease claim (treats, cures, heals acne, eczema, fungal infection)', 'Mechanism claim (collagen, melanin, DNA, cells, hormones)',
            'Professional endorsement (doctor, dermatologist, pharmacist fronting a cosmetic)', 'Prohibited sunscreen wording (sunblock, 100% protection, all-day)',
            'Safety claim (no side effects, chemical-free, 100% safe)', 'Absolute or permanent result (whitening in 3 days, permanent)',
            'Comparison or disparagement of another brand', 'Before-and-after without the time elapsed', 'GMP / MOH / KKM-approved wording on a notified cosmetic',
            'Prohibited ingredient or procedure reference (hydroquinone, steroid, injection)', 'Unsubstantiated quantitative claim (99% effective, 10x)'],
    fields: ['Nama Kosmetik', 'Nombor Notifikasi', 'Jenis Aduan', 'Platform', 'Post URL', 'Deskripsi Aduan', 'Screenshot Link'],
    channel: { label: 'Borang Pelaporan Aduan Kosmetik Bernotifikasi (Google Form, KKM)', url: '', checked: '2026-10-09', status: 'template',
               note: 'The pre-fill template lives in Script Properties KKM_FORM_URL (set from the dashboard). Paper form: Borang Aduan Produk Kosmetik Bernotifikasi, npra.gov.my.' },
    email: 'aduankos@npra.gov.my', phone: '03-7883 5400'
  },
  {
    value: 'Kualiti Kosmetik', kkm: true, group: 'KKM',
    agency: 'NPRA — Pusat Pematuhan dan Kawalan Kualiti, Seksyen Surveilan dan Aduan',
    act: 'Peraturan-Peraturan Kawalan Dadah dan Kosmetik 1984, Peraturan 18A (notifikasi kosmetik); Guidelines for Control of Cosmetic Products in Malaysia, Annex I Part 1 (notification) dan Annex II–VII (bahan terlarang/terhad)',
    basis: 'Produk ini disyaki tidak mematuhi Peraturan-Peraturan Kawalan Dadah dan Kosmetik 1984 dan Guidelines for Control of Cosmetic Products in Malaysia (NPRA).',
    ref: { label: 'Nombor Notifikasi (NOT)', hint: 'NOTxxxxxxxxK — verify on QUEST3+; blank if not notified', pattern: '^NOT\\s*\\d' },
    cases: ['Cosmetic sold without a notification (no NOT on QUEST3+)', 'Suspected adulteration (hydroquinone, tretinoin, mercury, steroid)',
            'Adverse reaction reported by a user', 'Prohibited or restricted ingredient on the label (Annex II / III)', 'Label missing NOT, ingredients, batch or notification holder',
            'Counterfeit or parallel-import cosmetic', 'Notification cancelled by NPRA but product still on sale'],
    fields: ['Nama Kosmetik', 'Nombor Notifikasi', 'Jenis Aduan', 'Platform', 'Post URL', 'Deskripsi Aduan', 'Screenshot Link'],
    channel: { label: 'Borang Pelaporan Aduan Kosmetik Bernotifikasi (Google Form, KKM)', url: '', checked: '2026-10-09', status: 'template',
               note: 'Same KKM form as Iklan Kosmetik; pick "Kualiti Kosmetik" on it. Sample or photo of the product helps.' },
    email: 'aduankos@npra.gov.my', phone: '03-7883 5400'
  },
  {
    value: 'Ubat / Suplemen (Lembaga Iklan Ubat)', group: 'KKM',
    agency: 'Bahagian Penguatkuasaan Farmasi (BPF), KKM — Seksyen Kawal Selia Iklan Ubat / Lembaga Iklan Ubat (LIU)',
    act: 'Akta Ubat (Iklan dan Penjualan) 1956 (Akta 290), Seksyen 3, 4, 4B; Peraturan-Peraturan Ubat (Iklan dan Penjualan) 1976 (kelulusan KKLIU); Peraturan-Peraturan Kawalan Dadah dan Kosmetik 1984 (pendaftaran MAL)',
    basis: 'Iklan ini disyaki melanggar Akta Ubat (Iklan dan Penjualan) 1956 (Akta 290) kerana disiarkan tanpa nombor kelulusan Lembaga Iklan Ubat (KKLIU) dan/atau membuat dakwaan rawatan penyakit yang dilarang.',
    ref: { label: 'No. Kelulusan Iklan KKLIU / No. Pendaftaran MAL', hint: 'KKLIU xxxx/2026 or MALxxxxxxxxA — blank if none shown', pattern: '^(KKLIU|MAL)' },
    cases: ['Supplement, traditional medicine or health product advertised with no KKLIU approval number', 'Claim to treat a disease in the Schedule of Act 290 (diabetes, hypertension, cancer, infertility, …)',
            'Unregistered product (no MAL number) advertised or sold', 'Expired KKLIU approval still in use (s.4B)', 'Scheduled poison (steroid, tretinoin, antibiotics) sold online',
            'Cosmetic advertised as a medicine: product is in fact a health product', 'Doctor or pharmacist endorsing a health product in an advertisement', 'Slimming or sexual-performance claims on a supplement'],
    fields: ['Nama Kosmetik', 'Nombor Notifikasi', 'Brand', 'Platform', 'Post URL', 'Date', 'Deskripsi Aduan', 'Screenshot Link'],
    channel: { label: 'SisPAA KKM (Sistem Pengurusan Aduan Awam) — named by BPF notice "Aduan Iklan Ubat dan Perkhidmatan Yang Tidak Patuh", 8 Jun 2026', url: 'https://moh.spab.gov.my/', checked: '2026-10-09', status: 'loads',
               note: 'Portal is a sign-in web app (register once). BPF: 03-7841 3200, Lot 36, Jalan Profesor Diraja Ungku Aziz, 46200 Petaling Jaya.' },
    email: '', phone: '03-7841 3200'
  },
  {
    value: 'MDA - Peranti Perubatan', group: 'KKM',
    agency: 'Pihak Berkuasa Peranti Perubatan (MDA), KKM',
    act: 'Akta Peranti Perubatan 2012 (Akta 737), Seksyen 5 (pendaftaran) dan Seksyen 40 (pelaporan); Peraturan-Peraturan Peranti Perubatan (Pengiklanan) 2019',
    basis: 'Peranti ini disyaki diiklankan bertentangan dengan Akta Peranti Perubatan 2012 (Akta 737) dan Peraturan-Peraturan Peranti Perubatan (Pengiklanan) 2019: tanpa nombor pendaftaran MDA dan/atau dengan dakwaan di luar tujuan penggunaan yang didaftarkan.',
    ref: { label: 'No. Pendaftaran Peranti MDA', hint: 'GAxxxx / GBxxxx / GCxxxx / GDxxxx — blank if none shown', pattern: '^G[ABCD]\\d' },
    cases: ['Device sold or advertised without an MDA registration number (HIFU, laser, LED mask, microneedling pen, dermaroller, IPL)', 'Dermal filler, thread or skin booster (a device) promoted to the public with treatment claims',
            'Claims beyond the registered intended use', 'Advertisement not approved under the 2019 Advertising Regulations', 'Home-use device promising clinical results',
            'Counterfeit or grey-import device', 'Device adverse event (burn, scarring, infection) reported by a user'],
    fields: ['Nama Kosmetik', 'Nombor Notifikasi', 'Brand', 'Platform', 'Post URL', 'Date', 'Deskripsi Aduan', 'Screenshot Link'],
    channel: { label: 'MDA FEMES — Feedback Management System (complaints, inquiries)', url: 'https://femes.mda.gov.my/', checked: '2026-10-09', status: 'loads',
               note: 'mda.gov.my "Customer Complaint Form" points here. Device safety complaints: device_complaint@mda.gov.my (MDA chatbot answer; unverified). Hotline 03-8230 0300.' },
    email: 'device_complaint@mda.gov.my', phone: '03-8230 0300'
  },
  {
    value: 'Amalan Perubatan (MMC)', group: 'KKM',
    agency: 'CKAPS — Cawangan Kawalan Amalan Perubatan Swasta, KKM (kemudahan dan iklan klinik); Majlis Perubatan Malaysia (MMC) (kelakuan pengamal)',
    act: 'Akta Kemudahan dan Perkhidmatan Jagaan Kesihatan Swasta 1998 (Akta 586), Seksyen 108 (iklan); Peraturan-Peraturan Kemudahan dan Perkhidmatan Jagaan Kesihatan Swasta (Hospital Swasta dan Kemudahan Jagaan Kesihatan Swasta Lain) 2006; Akta Ubat (Iklan dan Penjualan) 1956 (kelulusan Lembaga Iklan Ubat untuk iklan klinik/estetik); Akta Perubatan 1971 (Akta 50) dan Kod Kelakuan Profesional MMC; Garis Panduan Amalan Perubatan Estetik KKM',
    basis: 'Iklan ini disyaki melanggar Seksyen 108 Akta Kemudahan dan Perkhidmatan Jagaan Kesihatan Swasta 1998 (Akta 586) dan syarat Lembaga Iklan Ubat bagi iklan perkhidmatan perubatan/estetik, serta Kod Kelakuan Profesional Majlis Perubatan Malaysia.',
    ref: { label: 'No. Pendaftaran MMC / No. Pendaftaran Klinik (Borang B)', hint: 'MMC number of the doctor, or the clinic registration — blank if unknown', pattern: '' },
    cases: ['Clinic or doctor advertising aesthetic procedures (filler, botulinum toxin, threads, laser) with before-after, prices, discounts or packages',
            'Testimonials or guarantees of a medical outcome', 'Aesthetic procedure performed or advertised by a non-doctor (beautician, salon, spa)',
            'Doctor without an aesthetic LCP advertising aesthetic services', 'Unregistered or unlicensed clinic', 'Misleading clinic name or signboard (s.108)',
            'Doctor endorsing a product in a professional capacity (also an NPRA / BPF matter)', 'Mobile or online consultation sold outside a registered facility'],
    fields: ['Brand', 'Nombor Notifikasi', 'Platform', 'Post URL', 'Date', 'Deskripsi Aduan', 'Screenshot Link'],
    channel: { label: 'SisPAA CKAPS (myCKAPS) — complaints on private clinics and their advertising', url: 'https://myckaps.spab.gov.my/', checked: '2026-10-09', status: 'loads',
               note: 'Sign-in web app. Also SisPAA KKM moh.spab.gov.my. CKAPS phone 03-8883 1362 (RTM report; unverified). A practitioner-conduct complaint goes to MMC (mmc.gov.my).' },
    email: '', phone: '03-8883 1362'
  },
  {
    value: 'Makanan (BKKM)', group: 'KKM',
    agency: 'Bahagian Keselamatan dan Kualiti Makanan (BKKM), KKM',
    act: 'Akta Makanan 1983 (Akta 281), Seksyen 17 (pelabelan dan iklan mengelirukan); Peraturan-Peraturan Makanan 1985, Peraturan 18(6) (tuntutan rawatan penyakit dilarang), Peraturan 18A–18E (tuntutan pemakanan dan kesihatan)',
    basis: 'Iklan ini disyaki melanggar Seksyen 17 Akta Makanan 1983 (Akta 281) dan Peraturan 18(6) Peraturan-Peraturan Makanan 1985 kerana membuat tuntutan mencegah, merawat atau menyembuhkan penyakit bagi suatu makanan.',
    ref: { label: 'No. rujukan produk / pengilang (jika ada)', hint: 'MeSTI / HACCP / company registration if shown — blank otherwise', pattern: '' },
    cases: ['Food, drink or food-supplement claiming to prevent, treat or cure a disease (reg 18(6))', 'Slimming, detox or "burn fat" claims on a food', 'Lactation (milk booster) or fertility claims on a food',
            'Infant formula or follow-up formula advertised to the public', 'Nutrient or health claim not permitted by reg 18A–18E', 'Unregistered "health drink" or coffee mix with medicinal claims',
            'Misleading origin, ingredient or halal-looking wording on a food label'],
    fields: ['Nama Kosmetik', 'Brand', 'Platform', 'Post URL', 'Date', 'Deskripsi Aduan', 'Screenshot Link'],
    channel: { label: 'SisPAA KKM (Sistem Pengurusan Aduan Awam)', url: 'https://moh.spab.gov.my/', checked: '2026-10-09', status: 'loads',
               note: 'BKKM hotline 03-8885 0797 / MyGCC 03-8000 8000 and Facebook BKKMHQ (2025 reports; unverified). A District Health Office (PKD) also takes it.' },
    email: '', phone: '03-8885 0797'
  },
  {
    value: 'Perubatan Tradisional & Komplementari (BPTK)', group: 'KKM',
    agency: 'Bahagian Perubatan Tradisional dan Komplementari (BPTK), KKM — Cawangan Inspektorat dan Penguatkuasaan',
    act: 'Akta Perubatan Tradisional dan Komplementari 2016 (Akta 775), Bahagian IX (penguatkuasaan, berkuat kuasa 1 Ogos 2024); Garis Panduan Pengiklanan Pengamal PT&K (BPTK, 2025); Akta Ubat (Iklan dan Penjualan) 1956 bagi dakwaan produk',
    basis: 'Iklan ini disyaki melanggar Akta Perubatan Tradisional dan Komplementari 2016 (Akta 775) dan Garis Panduan Pengiklanan Pengamal PT&K kerana menawarkan rawatan penyakit oleh pengamal yang tidak berdaftar dan/atau dengan dakwaan yang mengelirukan.',
    ref: { label: 'No. Pendaftaran Pengamal PT&K', hint: 'Practitioner registration under Act 775 — blank if none shown', pattern: '' },
    cases: ['Urut, bekam, akupunktur, homeopati or Islamic-medicine practitioner advertising a cure for a disease', 'Unregistered T&CM practitioner or premises (after 28 Feb 2025)',
            'T&CM practitioner promoting a product with medicinal claims (also a BPF matter)', 'Testimonials or guarantees of a cure'],
    fields: ['Brand', 'Nombor Notifikasi', 'Platform', 'Post URL', 'Date', 'Deskripsi Aduan', 'Screenshot Link'],
    channel: { label: 'SisPAA KKM (Sistem Pengurusan Aduan Awam)', url: 'https://moh.spab.gov.my/', checked: '2026-10-09', status: 'loads',
               note: 'BPTK portal hq.moh.gov.my/tcm answered HTTP 500 at check time; its advertising guideline is under Guideline → Advertisement.' },
    email: '', phone: ''
  },
  {
    value: 'Produk Merokok / Vape (Akta 852)', group: 'KKM',
    agency: 'Kementerian Kesihatan Malaysia — penguatkuasaan Akta 852 (Bahagian Kawalan Penyakit, Sektor Kawalan Tembakau)',
    act: 'Akta Kawalan Produk Merokok Demi Kesihatan Awam 2024 (Akta 852), Seksyen 7–10 (iklan, promosi, tajaan); berkuat kuasa 1 Oktober 2024',
    basis: 'Iklan atau promosi ini disyaki melanggar Seksyen 7 hingga 10 Akta Kawalan Produk Merokok Demi Kesihatan Awam 2024 (Akta 852).',
    ref: { label: 'No. Pendaftaran Produk (Akta 852)', hint: 'Registration under Act 852 if shown — blank otherwise', pattern: '' },
    cases: ['Online sale, advertisement or promotion of vape / e-liquid / tobacco', 'Influencer or sponsorship promoting a smoking product', 'Sale to a minor', 'Unregistered smoking product', 'Flavour or lifestyle marketing aimed at young people'],
    fields: ['Brand', 'Nama Kosmetik', 'Platform', 'Post URL', 'Date', 'Deskripsi Aduan', 'Screenshot Link'],
    channel: { label: 'SisPAA KKM (Sistem Pengurusan Aduan Awam)', url: 'https://moh.spab.gov.my/', checked: '2026-10-09', status: 'loads', note: 'No dedicated vape complaint form was found on 9 Oct 2026; SisPAA is the MOH-wide channel.' },
    email: '', phone: ''
  },
  {
    value: 'Halal (JAKIM / KPDN)', group: 'Other',
    agency: 'JAKIM — Bahagian Pengurusan Halal (sijil dan logo); KPDN — Bahagian Penguatkuasa (pendakwaan di bawah Akta Perihal Dagangan 2011)',
    act: 'Akta Perihal Dagangan 2011 (Akta 730); Perintah Perihal Dagangan (Takrif Halal) 2011; Perintah Perihal Dagangan (Perakuan dan Penandaan Halal) 2011; Manual Prosedur Pensijilan Halal Malaysia (Domestik)',
    basis: 'Penggunaan perihal atau logo halal ini disyaki melanggar Perintah Perihal Dagangan (Takrif Halal) 2011 dan Perintah Perihal Dagangan (Perakuan dan Penandaan Halal) 2011 di bawah Akta Perihal Dagangan 2011 (Akta 730).',
    ref: { label: 'No. Sijil Halal (SPHM) / kod pengesahan', hint: 'From the Halal Malaysia portal verification — blank if none', pattern: '' },
    cases: ['Fake, expired or altered Malaysian halal logo', 'Foreign halal logo not recognised by JAKIM', '"Halal" claim without a Sijil Pengesahan Halal Malaysia (SPHM)',
            'Product sold under a certified brand but outside the certificate scope', 'Cosmetic or food claiming halal with no certificate', 'Premises claiming halal certification it does not hold'],
    fields: ['Brand', 'Nama Kosmetik', 'Nombor Notifikasi', 'Platform', 'Post URL', 'Date', 'Deskripsi Aduan', 'Screenshot Link'],
    channel: { label: 'E-Aduan JAKIM (SisPAA Islam) — linked from halal.gov.my "E-Aduan"', url: 'https://islam.spab.gov.my/eApps/system/index.do', checked: '2026-10-09', status: 'loads',
               note: 'Enforcement and prosecution are KPDN: e-aduan.kpdn.gov.my (answered 503 at check time), 1-800-886-800. JAKIM halal hub: pr_halal@islam.gov.my, 03-8892 5000.' },
    email: 'pr_halal@islam.gov.my', phone: '03-8892 5000'
  },
  {
    value: 'Pengguna / Perihal Dagangan (KPDN)', group: 'Other',
    agency: 'Kementerian Perdagangan Dalam Negeri dan Kos Sara Hidup (KPDN) — Bahagian Penguatkuasa',
    act: 'Akta Perihal Dagangan 2011 (Akta 730), Seksyen 5 (perihal dagangan palsu); Akta Perlindungan Pengguna 1999 (Akta 599), Seksyen 10 (representasi palsu atau mengelirukan); Peraturan-Peraturan Perlindungan Pengguna (Urus Niaga Perdagangan Elektronik) 2012',
    basis: 'Iklan ini disyaki membuat perihal dagangan palsu atau representasi mengelirukan yang menyalahi Akta Perihal Dagangan 2011 (Akta 730) dan Akta Perlindungan Pengguna 1999 (Akta 599).',
    ref: { label: 'No. Pendaftaran Syarikat (SSM) jika diketahui', hint: 'Seller or company registration — blank if unknown', pattern: '' },
    cases: ['False claims about origin, maker or award ("No.1", "clinically proven" without proof)', 'Fake reviews, fabricated testimonials or paid endorsements undisclosed',
            'Fake discount, misleading price or profiteering', 'Counterfeit product', 'Online seller with no identity, address or return terms (2012 e-commerce regulations)', 'Non-delivery or scam after payment'],
    fields: ['Brand', 'Nama Kosmetik', 'Nombor Notifikasi', 'Platform', 'Post URL', 'Date', 'Deskripsi Aduan', 'Screenshot Link'],
    channel: { label: 'e-Aduan KPDN', url: 'https://e-aduan.kpdn.gov.my/', checked: '2026-10-09', status: 'down',
               note: 'Not reachable on 9 Oct 2026 from three vantage points outside Malaysia (the checking session: 503 then timeout; a sandbox and a GitHub runner: timeout and TLS EOF on e-aduan, www and eaduan). It may be fenced to Malaysian addresses, so try it from your own browser. Re-checked every few hours until it answers. Call centre 1-800-886-800; WhatsApp 019-279 4317 and the Ez ADU app (2020 reports; unverified).' },
    email: 'e-aduan@kpdnhep.gov.my', phone: '1-800-886-800'
  },
  {
    value: 'Kandungan Dalam Talian (MCMC)', group: 'Other',
    agency: 'Suruhanjaya Komunikasi dan Multimedia Malaysia (MCMC / SKMM)',
    act: 'Akta Komunikasi dan Multimedia 1998 (Akta 588), Seksyen 211 dan 233 (kandungan yang menyalahi undang-undang); Kod Kandungan Komunikasi dan Multimedia (CMCF); arahan Menteri Komunikasi 19 Sep 2026: aduan iklan kesihatan dalam talian yang mengelirukan dirujuk kepada MCMC',
    basis: 'Kandungan ini disyaki menyalahi Akta Komunikasi dan Multimedia 1998 (Akta 588) dan Kod Kandungan Komunikasi dan Multimedia, dan dirujuk untuk tindakan penurunan kandungan.',
    ref: { label: 'No. rujukan aduan MCMC', hint: 'Filled after the portal issues one', pattern: '' },
    cases: ['Takedown of an online health advertisement already found non-compliant by KKM (BPF / NPRA / CKAPS)', 'Impersonation of a doctor, clinic or brand', 'Deepfake or AI endorsement by a public figure',
            'Scam page selling health products', 'Repeat offender whose page keeps re-posting a banned advertisement'],
    fields: ['Brand', 'Platform', 'Post URL', 'Date', 'Deskripsi Aduan', 'Screenshot Link'],
    channel: { label: 'Portal Aduan MCMC (Consumer Redress Portal) — register, verify e-mail, then New Complaint', url: 'https://aduan.mcmc.gov.my/', checked: '2026-10-09', status: 'loads',
               note: 'Loads as "MCMC - CRP Portal". Hotline 1-800-188-030 (weekdays 8:30–17:30). The older aduan.skmm.gov.my address is superseded.' },
    email: '', phone: '1-800-188-030'
  },
  {
    value: 'Lain-lain', group: 'Other',
    agency: 'Other — name the agency in Remarks',
    act: '',
    basis: '',
    ref: { label: 'Reference number', hint: 'Whatever the agency asks for', pattern: '' },
    cases: ['Anything that fits none of the routes above — write the agency and the instrument in Remarks'],
    fields: ['Brand', 'Nama Kosmetik', 'Platform', 'Post URL', 'Date', 'Deskripsi Aduan', 'Screenshot Link'],
    channel: { label: '', url: '', checked: '', status: 'none', note: 'Paste the agency\'s form or portal link as this route\'s form.' },
    email: '', phone: ''
  }
];
var JENIS_ADUAN = ROUTES.map(function (r) { return r.value; });

function routeOf_(v) {
  for (var i = 0; i < ROUTES.length; i++) if (ROUTES[i].value === v) return ROUTES[i];
  return ROUTES[0];
}

// Per-route form templates, kept in Script Properties so they appear in every browser.
// { "<route value>": "<url or pre-fill template>" }. The KKM cosmetic template stays in KKM_FORM_URL.
function routeForms_() {
  try { return JSON.parse(PROPS.getProperty('ROUTE_FORMS') || '{}') || {}; } catch (err) { return {}; }
}
function setRouteForm_(value, url) {
  if (JENIS_ADUAN.indexOf(value) < 0) throw new Error('Unknown route: ' + value);
  url = String(url || '').trim();
  if (url && !/^https?:\/\/\S+$/i.test(url)) throw new Error('The link must start with https://');
  var m = routeForms_();
  if (url) m[value] = url; else delete m[value];
  PROPS.setProperty('ROUTE_FORMS', JSON.stringify(m));
  return { ok: true, forms: m };
}
function routesPayload_() {
  return { routes: ROUTES, forms: routeForms_() };
}

var DEFAULT_BRANDS = ['La Roche-Posay', 'Eucerin', 'QV', 'The Raw'];
var DEFAULT_PLATFORMS = ['Instagram', 'Facebook', 'Threads'];
var DEFAULT_VIOLATION_TYPES = [
  'Medicinal / disease claim',
  'Mechanism claim (collagen, melanin, DNA, cells)',
  'Professional endorsement (doctor / dermatologist)',
  'Prohibited sunscreen wording',
  'Safety / no-side-effect claim',
  'Absolute / permanent result',
  'Comparison or disparagement',
  'Before-after without time elapsed',
  'GMP / MOH / approval reference',
  'Prohibited ingredient or procedure reference',
  'Unsubstantiated quantitative claim',
  'Other'
];

// Targets tab: the brands and handles the scraper visits. Edit this in the sheet, not in config.yaml.
// Columns after "Active" and before "Product hints" are platforms; add a column to add a platform.
var TARGET_SHEET_NAME = 'Targets';
var TARGET_FIXED_HEAD = ['Brand', 'Active'];
var TARGET_FIXED_TAIL = ['Product hints', 'Notes'];
// Every other column is read as a platform, so any column that is not one has to be named here.
// 'Type' marks what the account is: a brand's own page, or a doctor / KOL who promotes products.
var TARGET_META_COLUMNS = ['Type', 'Website feed', 'Product feed'];
var DEFAULT_TARGET_PLATFORMS = ['Instagram', 'Facebook', 'Threads'];
var DEFAULT_TARGETS = [
  ['La Roche-Posay', true, 'larocheposaymy', 'LaRochePosayMalaysia', 'larocheposaymy', 'Effaclar, Cicaplast, Anthelios, Toleriane, Lipikar, Mela B3, Hyalu B5', ''],
  ['Eucerin', true, 'eucerin_malaysia', 'EucerinMalaysia', 'eucerin_malaysia', 'Spotless Brightening, Ultrasensitive, Atopicontrol, Sun Gel-Creme, Dermopurifyer, Urea Repair, Hyaluron-Filler', ''],
  ['QV', true, 'qvskincaremy', 'QVSkincareMalaysia', 'qvskincaremy', 'QV Gentle Wash, QV Cream, QV Face, QV Baby, QV Intensive', ''],
  ['The Raw', true, 'therawmy', 'therawmy', 'therawmy', '', '']
];

var SCREENSHOT_FOLDER_NAME = 'KKM Complaint Screenshots';
var EXPORT_FOLDER_NAME = 'KKM Complaint Exports';
var MAX_ROWS_RETURNED = 2000;
var EXPORT_PAGE_MAX = 500;      // rows per `export` page; the dashboard pages through
var EXPORT_DOC_MAX_ROWS = 400;  // rows a register document will carry
var EXPORT_DOC_MAX_IMAGES = 30; // screenshots embedded in one document (Drive reads are the slow part)
var PROPS = PropertiesService.getScriptProperties();
var TZ = 'Asia/Kuala_Lumpur';

// ---------------------------------------------------------------------------
// One-time setup
// ---------------------------------------------------------------------------
function setup() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = getOrCreateSheet_(ss, SHEET_NAME);
  ensureHeaders_(sheet);
  var lookups = getOrCreateSheet_(ss, LOOKUP_SHEET_NAME);
  ensureLookups_(lookups);
  applyValidation_(sheet, lookups);
  ensureTargets_(ss);
  ensureFolder_();
  if (!PROPS.getProperty('API_TOKEN')) PROPS.setProperty('API_TOKEN', randomToken_(40));
  if (!PROPS.getProperty('DASHBOARD_KEY')) PROPS.setProperty('DASHBOARD_KEY', randomToken_(24));
  if (!PROPS.getProperty('OWNER_EMAIL')) PROPS.setProperty('OWNER_EMAIL', Session.getEffectiveUser().getEmail());
  if (!PROPS.getProperty('KKM_FORM_URL')) PROPS.setProperty('KKM_FORM_URL', '');
  if (!PROPS.getProperty('ROUTE_FORMS')) PROPS.setProperty('ROUTE_FORMS', '{}');
  if (!PROPS.getProperty('SCHEMA_VERSION')) PROPS.setProperty('SCHEMA_VERSION', '1');
  Logger.log('Setup complete.\nAPI_TOKEN=%s\nDASHBOARD_KEY=%s\nOWNER_EMAIL=%s',
    PROPS.getProperty('API_TOKEN'), PROPS.getProperty('DASHBOARD_KEY'), PROPS.getProperty('OWNER_EMAIL'));
  return { ok: true, apiToken: PROPS.getProperty('API_TOKEN'), dashboardKey: PROPS.getProperty('DASHBOARD_KEY') };
}

function onOpen() {
  SpreadsheetApp.getUi().createMenu('KKM Complaints')
    .addItem('Run setup / repair headers', 'setup')
    .addItem('Show API token + dashboard key', 'showSecrets_')
    .addItem('Rotate API token', 'rotateApiToken_')
    .addToUi();
}

function onEdit(e) {
  // Wan often pastes NPRA's reply straight into the sheet rather than through the dashboard.
  // This is a simple trigger, so it installs itself and runs as him - no setup, no extra
  // authorisation. It touches only the two cells it needs, never the whole row, so it cannot
  // collide with a scraper insert happening at the same moment.
  try {
    if (!e || !e.range) return;
    var sheet = e.range.getSheet();
    if (sheet.getName() !== SHEET_NAME) return;
    if (e.range.getColumn() !== COL['KKM Feedback'] + 1) return;
    var r = e.range.getRow();
    if (r < 2) return;
    if (String(e.range.getValue() || '').trim() === '') return;   // cleared: row returns to the list

    var statusCell = sheet.getRange(r, COL['Status'] + 1);
    if (String(statusCell.getValue()) === 'Dismissed') return;
    if (String(statusCell.getValue()) !== 'Complete') statusCell.setValue('Complete');

    var dateCell = sheet.getRange(r, COL['Tarikh Melapor'] + 1);
    if (!dateCell.getValue()) dateCell.setValue(Utilities.formatDate(new Date(), TZ, 'yyyy-MM-dd'));

    sheet.getRange(r, COL['Updated At'] + 1).setValue(nowIso_());
  } catch (err) {
    console.error(err);   // never let this block the edit itself
  }
}

function showSecrets_() {
  SpreadsheetApp.getUi().alert(
    'API_TOKEN (scraper):\n' + PROPS.getProperty('API_TOKEN') +
    '\n\nDASHBOARD_KEY (append ?key=… to the web app URL):\n' + PROPS.getProperty('DASHBOARD_KEY'));
}

function rotateApiToken_() {
  PROPS.setProperty('API_TOKEN', randomToken_(40));
  showSecrets_();
}

function getOrCreateSheet_(ss, name) {
  return ss.getSheetByName(name) || ss.insertSheet(name);
}

function ensureHeaders_(sheet) {
  var lastCol = Math.max(sheet.getLastColumn(), 1);
  var existing = sheet.getRange(1, 1, 1, lastCol).getValues()[0].map(function (h) { return String(h).trim(); });
  var same = existing.length >= HEADERS.length && HEADERS.every(function (h, i) { return existing[i] === h; });
  if (!same) {
    // Insert any header that is missing at its own position, so the sheet's own data moves
    // with it. Writing the header row straight over the old one would silently misalign every
    // existing row by the number of inserted columns.
    var hasData = sheet.getLastRow() > 1;
    if (hasData && existing.filter(String).length) {
      HEADERS.forEach(function (h, i) {
        if (existing.indexOf(h) >= 0) return;              // already somewhere, leave it
        if (i === 0) sheet.insertColumnBefore(1); else sheet.insertColumnAfter(i);
        sheet.getRange(1, i + 1).setValue(h);
        existing.splice(i, 0, h);
        console.log('Schema migration: inserted column "' + h + '" at position ' + (i + 1));
      });
    }
    sheet.getRange(1, 1, 1, HEADERS.length).setValues([HEADERS]);
  }
  var hdr = sheet.getRange(1, 1, 1, HEADERS.length);
  hdr.setFontWeight('bold').setBackground('#0f172a').setFontColor('#ffffff').setWrap(false);
  sheet.setFrozenRows(1);
  var widths = [150, 95, 130, 100, 260, 220, 380, 220, 380, 100, 220, 200, 150, 130, 420, 110, 320, 80, 90, 160, 160];
  widths.forEach(function (w, i) { sheet.setColumnWidth(i + 1, w); });
  if (sheet.getMaxColumns() > HEADERS.length) {
    sheet.deleteColumns(HEADERS.length + 1, sheet.getMaxColumns() - HEADERS.length);
  }
}

function ensureLookups_(sheet) {
  var lists = [
    ['Brands', DEFAULT_BRANDS],
    ['Platforms', DEFAULT_PLATFORMS],
    ['Violation Types', DEFAULT_VIOLATION_TYPES],
    ['Statuses', STATUSES],
    ['Jenis Aduan', JENIS_ADUAN]
  ];
  lists.forEach(function (pair, c) {
    var col = c + 1;
    if (sheet.getRange(1, col).getValue() !== pair[0]) {
      sheet.getRange(1, col).setValue(pair[0]).setFontWeight('bold');
    }
    // Only seed when the column is empty below the header; never overwrite the user's edits.
    var below = sheet.getRange(2, col, Math.max(sheet.getLastRow() - 1, 1), 1).getValues()
      .map(function (r) { return r[0]; }).filter(String);
    if (below.length === 0) {
      sheet.getRange(2, col, pair[1].length, 1).setValues(pair[1].map(function (v) { return [v]; }));
    }
    sheet.setColumnWidth(col, 260);
  });
  sheet.setFrozenRows(1);
}

function ensureTargets_(ss) {
  var sheet = ss.getSheetByName(TARGET_SHEET_NAME);
  var created = false;
  if (!sheet) { sheet = ss.insertSheet(TARGET_SHEET_NAME); created = true; }
  if (sheet.getLastRow() === 0) {
    var headers = TARGET_FIXED_HEAD.concat(DEFAULT_TARGET_PLATFORMS, TARGET_FIXED_TAIL);
    sheet.getRange(1, 1, 1, headers.length).setValues([headers])
      .setFontWeight('bold').setBackground('#0f172a').setFontColor('#ffffff');
    sheet.getRange(2, 1, DEFAULT_TARGETS.length, headers.length).setValues(DEFAULT_TARGETS);
    sheet.setFrozenRows(1);
    [170, 70, 180, 200, 180, 360, 240].forEach(function (w, i) { sheet.setColumnWidth(i + 1, w); });
    created = true;
  }
  // Active column as checkboxes
  var rows = Math.max(sheet.getMaxRows() - 1, 1);
  sheet.getRange(2, 2, rows, 1).setDataValidation(SpreadsheetApp.newDataValidation().requireCheckbox().build());
  return sheet;
}

function sanitizeHandle_(raw) {
  // A Targets cell should hold a bare handle, but it's easy to paste the full profile URL
  // instead (e.g. copied straight from the address bar). Reduce either form to just the
  // handle so `profile_url.format(handle=...)` never doubles the domain.
  if (raw === null || raw === undefined) return '';
  var v = String(raw).trim();
  if (!v) return '';
  var looksLikeUrl = /^https?:\/\//i.test(v) || /(?:^|\.)(?:instagram|facebook|threads)\.(?:com|net)\b/i.test(v);
  if (!looksLikeUrl) return v.replace(/^\/+|\/+$/g, '').replace(/^@/, '');
  var withoutScheme = v.replace(/^https?:\/\//i, '');
  var slash = withoutScheme.indexOf('/');
  var path = slash === -1 ? '' : withoutScheme.slice(slash);
  path = path.split(/[?#]/)[0];
  var IGNORE = ['popular', 'explore', 'reel', 'reels', 'p', 'stories', 'tv', 'tags', 'hashtag', 'profile.php'];
  var segments = path.split('/')
    .filter(function (s) { return s.length > 0; })
    .filter(function (s) { return IGNORE.indexOf(s.toLowerCase()) < 0; });
  var handle = segments.length ? segments[segments.length - 1] : '';
  return handle.replace(/^@/, '');
}

function targets_() {
  // Reads the Targets tab. Returns [{name, active, handles: {Platform: handle}, product_hints: [..], notes}].
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ensureTargets_(ss);
  var last = sheet.getLastRow();
  if (last < 2) return [];
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0].map(function (h) { return String(h).trim(); });
  var iBrand = headers.indexOf('Brand'), iActive = headers.indexOf('Active');
  var iHints = headers.indexOf('Product hints'), iNotes = headers.indexOf('Notes');
  var iType = headers.indexOf('Type');
  var platformCols = [];
  headers.forEach(function (h, i) {
    if (h && TARGET_FIXED_HEAD.indexOf(h) < 0 && TARGET_FIXED_TAIL.indexOf(h) < 0 &&
        TARGET_META_COLUMNS.indexOf(h) < 0) platformCols.push({ name: h, i: i });
  });
  var out = [];
  sheet.getRange(2, 1, last - 1, headers.length).getValues().forEach(function (row) {
    var name = String(row[iBrand] || '').trim();
    if (!name) return;
    var active = iActive >= 0 ? (row[iActive] === true || String(row[iActive]).toLowerCase() === 'true') : true;
    var handles = {};
    platformCols.forEach(function (pc) {
      var v = sanitizeHandle_(row[pc.i]);
      if (v) handles[pc.name] = v;
    });
    var hints = iHints >= 0 ? String(row[iHints] || '').split(',').map(function (x) { return x.trim(); }).filter(String) : [];
    var type = iType >= 0 ? String(row[iType] || '').trim() : '';
    var iWeb = headers.indexOf('Website feed'), iProd = headers.indexOf('Product feed');
    out.push({ name: name, active: active, handles: handles, product_hints: hints, type: type,
               website_feed: iWeb >= 0 ? String(row[iWeb] || '').trim() : '',
               product_feed: iProd >= 0 ? String(row[iProd] || '').trim() : '',
               notes: iNotes >= 0 ? String(row[iNotes] || '') : '' });
  });
  return out;
}

function applyValidation_(sheet, lookups) {
  var rows = Math.max(sheet.getMaxRows() - 1, 1);
  function rule(rangeA1) {
    return SpreadsheetApp.newDataValidation()
      .requireValueInRange(lookups.getRange(rangeA1), true).setAllowInvalid(true).build();
  }
  sheet.getRange(2, COL['Brand'] + 1, rows, 1).setDataValidation(rule('A2:A200'));
  sheet.getRange(2, COL['Platform'] + 1, rows, 1).setDataValidation(rule('B2:B200'));
  sheet.getRange(2, COL['Violation Type'] + 1, rows, 1).setDataValidation(rule('C2:C200'));
  sheet.getRange(2, COL['Status'] + 1, rows, 1).setDataValidation(
    SpreadsheetApp.newDataValidation().requireValueInList(STATUSES, true).setAllowInvalid(false).build());
  sheet.getRange(2, COL['Jenis Aduan'] + 1, rows, 1).setDataValidation(
    SpreadsheetApp.newDataValidation().requireValueInList(JENIS_ADUAN, true).setAllowInvalid(true).build());
}

function ensureFolder_() {
  var id = PROPS.getProperty('SCREENSHOT_FOLDER_ID');
  if (id) {
    try { return DriveApp.getFolderById(id); } catch (err) { /* recreate below */ }
  }
  var it = DriveApp.getFoldersByName(SCREENSHOT_FOLDER_NAME);
  var folder = it.hasNext() ? it.next() : DriveApp.createFolder(SCREENSHOT_FOLDER_NAME);
  PROPS.setProperty('SCREENSHOT_FOLDER_ID', folder.getId());
  return folder;
}

function ensureExportFolder_() {
  var id = PROPS.getProperty('EXPORT_FOLDER_ID');
  if (id) {
    try { return DriveApp.getFolderById(id); } catch (err) { /* recreate below */ }
  }
  var it = DriveApp.getFoldersByName(EXPORT_FOLDER_NAME);
  var folder = it.hasNext() ? it.next() : DriveApp.createFolder(EXPORT_FOLDER_NAME);
  PROPS.setProperty('EXPORT_FOLDER_ID', folder.getId());
  return folder;
}

// ---------------------------------------------------------------------------
// HTTP entry points
// ---------------------------------------------------------------------------
function doGet(e) {
  e = e || {}; var p = e.parameter || {};
  if (p.view === 'guide') {
    return serveGuide_(p);
  }
  if (p.action) {
    return jsonResponse_(handleApi_(p.action, p, p));
  }
  if (!isAuthorisedViewer_(p.key)) {
    return HtmlService.createHtmlOutput(
      '<!doctype html><html><body style="font-family:system-ui;padding:40px;color:#0f172a">' +
      '<h2>KKM Complaint Dashboard</h2><p>Access key missing or wrong. Open the URL with ' +
      '<code>?key=&lt;DASHBOARD_KEY&gt;</code> (see Script Properties).</p></body></html>')
      .setTitle('KKM Complaints — locked');
  }
  var t = HtmlService.createTemplateFromFile('Index');
  t.dashboardKey = p.key || '';
  t.kkmFormUrl = PROPS.getProperty('KKM_FORM_URL') || '';
  t.guideUrl = ScriptApp.getService().getUrl() + '?view=guide';
  return t.evaluate()
    .setTitle('KKM Cosmetic Complaints')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1')
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

function doPost(e) {
  var body;
  try {
    body = JSON.parse((e && e.postData && e.postData.contents) || '{}');
  } catch (err) {
    return jsonResponse_({ ok: false, error: 'Invalid JSON body' }, 400);
  }
  var token = body.token || (e.parameter && e.parameter.token);
  var key = body.key || (e.parameter && e.parameter.key);
  var action = body.action || 'insert';
  if (action !== 'ping' && !checkToken_(token) && !isAuthorisedViewer_(key)) {
    return jsonResponse_({ ok: false, error: 'Unauthorised' }, 401);
  }
  return jsonResponse_(handleApi_(action, body, { token: token, key: key }));
}

function handleApi_(action, body, p) {
  p = p || {}; body = body || {};
  var machine = checkToken_(p.token);
  var viewer = machine || isAuthorisedViewer_(p.key);
  var deny = { ok: false, error: 'Unauthorised' };
  try {
    switch (action) {
      case 'ping':          return { ok: true, time: new Date().toISOString(), schema: PROPS.getProperty('SCHEMA_VERSION') || '1' };
      // --- scraper (API_TOKEN) ---
      case 'insert':        if (!machine) return deny;
                            return apiInsertRecords_(body.records || (body.record ? [body.record] : []), 'scraper');
      case 'targets':       if (!viewer) return deny;
                            return { ok: true, targets: targets_() };
      case 'known_urls':    if (!viewer) return deny;
                            return { ok: true, urls: knownUrls_() };
      case 'seen_urls':     if (!viewer) return deny;
                            return { ok: true, urls: seenUrls_() };
      case 'mark_seen':     if (!machine) return deny;
                            return markSeen_(body.entries || []);
      // --- dashboard (DASHBOARD_KEY) or scraper ---
      case 'list':          if (!viewer) return deny;
                            return { ok: true, rows: listRecords_(p.status || body.status, p.brand || body.brand, p.platform || body.platform, p.q || body.q), stats: stats_() };
      case 'get':           if (!viewer) return deny;
                            return { ok: true, row: getRecord_(p.id || body.id) };
      case 'stats':         if (!viewer) return deny;
                            return { ok: true, stats: stats_() };
      case 'summary':       if (!viewer) return deny;
                            return { ok: true, summary: summary_() };
      case 'update_status': if (!viewer) return deny;
                            return updateStatus_(body.id, body.status, body.remarks);
      case 'bootstrap':     if (!viewer) return deny;
                            return { ok: true, rows: listRecords_(), stats: stats_(), lookups: lookups_(),
                                     kkmFormUrl: PROPS.getProperty('KKM_FORM_URL') || '',
                                     questUrl: 'https://quest3plus.bpfk.gov.my/pmo2/index.php', statuses: STATUSES,
                                     routes: routesPayload_(), summary: summary_() };
      case 'open':          if (!viewer) return deny;
                            return { ok: true, row: openRecord_(body.id) };
      case 'update_fields': if (!viewer) return deny;
                            return updateFields_(body.id, body.fields || {});
      case 'insert_manual': if (!viewer) return deny;
                            return insertManual_(body.record || {});
      case 'upload_screenshot': if (!viewer) return deny;
                            return { ok: true, link: saveScreenshot_(body.base64, body.mime, body.filename) };
      case 'set_form_url':  if (!viewer) return deny;
                            PROPS.setProperty('KKM_FORM_URL', String(body.url || '')); return { ok: true };
      case 'draft_review':  if (!viewer) return deny;
                            return draftReview_(body);
      // --- routes (the catalogue above) ---
      case 'routes':        if (!viewer) return deny;
                            return { ok: true, routes: routesPayload_() };
      case 'set_route_form': if (!viewer) return deny;
                            return setRouteForm_(body.route, body.url);
      // --- exports ---
      case 'export':        if (!viewer) return deny;
                            return exportRows_(body);
      case 'export_doc':    if (!viewer) return deny;
                            return exportDoc_(body);
      // --- Links tab (Links.gs): dashboard adds and watches, the scheduled workflow claims and reports ---
      case 'links_list':    if (!viewer) return deny;
                            return linksList_();
      case 'links_add':     if (!viewer) return deny;
                            return linksAdd_(body.links, body.note);
      case 'links_retry':   if (!viewer) return deny;
                            return linksRetry_(body.link);
      case 'links_detail':  if (!viewer) return deny;
                            return linksDetail_(body.link);
      case 'links_file':    if (!viewer) return deny;
                            return linksFile_(body.link);
      case 'links_dismiss': if (!viewer) return deny;
                            return linksDismiss_(body.link);
      case 'links_pending': if (!machine) return deny;
                            return linksPending_();
      case 'links_update':  if (!machine) return deny;
                            return linksUpdate_(body.updates);
      default:              return { ok: false, error: 'Unknown action: ' + action };
    }
  } catch (err) {
    console.error(err);
    return { ok: false, error: String(err && err.message || err) };
  }
}

// ---------------------------------------------------------------------------
// Reviewer for manual entries: the same rulebook the scraper uses, called from
// the dashboard so a post pasted by hand comes back in the NPRA report format.
// Needs ANTHROPIC_API_KEY in Script Properties; ANTHROPIC_MODEL is optional.
// ---------------------------------------------------------------------------
function draftReview_(body) {
  var key = PROPS.getProperty('ANTHROPIC_API_KEY');
  if (!key) {
    return { ok: false, error: 'ANTHROPIC_API_KEY is not set. Apps Script → Project Settings → Script properties.' };
  }
  var text = String(body.text || '').trim();
  var b64 = String(body.imageBase64 || '');
  if (!text && !b64) return { ok: false, error: 'Paste the post text, attach the screenshot, or both.' };

  var parts = [];
  if (body.brand) parts.push('Brand: ' + body.brand);
  if (body.platform) parts.push('Platform: ' + body.platform);
  if (body.url) parts.push('Post URL: ' + body.url);
  if (body.productHints) parts.push('Known product lines: ' + body.productHints);
  parts.push('');
  parts.push(text ? 'POST TEXT:\n' + text : 'POST TEXT: (none supplied — read the screenshot)');

  var content = [];
  if (b64) {
    content.push({
      type: 'image',
      source: { type: 'base64', media_type: String(body.imageMime || 'image/png'), data: b64.replace(/^data:[^,]+,/, '') }
    });
  }
  content.push({ type: 'text', text: parts.join('\n') });

  var res = UrlFetchApp.fetch('https://api.anthropic.com/v1/messages', {
    method: 'post',
    contentType: 'application/json',
    muteHttpExceptions: true,
    headers: { 'x-api-key': key, 'anthropic-version': '2023-06-01' },
    payload: JSON.stringify({
      model: PROPS.getProperty('ANTHROPIC_MODEL') || 'claude-haiku-4-5',
      max_tokens: 2000,
      system: NPRA_SYSTEM_PROMPT,
      messages: [{ role: 'user', content: content }]
    })
  });
  if (res.getResponseCode() !== 200) {
    throw new Error('Reviewer API ' + res.getResponseCode() + ': ' + res.getContentText().slice(0, 300));
  }
  var body_ = JSON.parse(res.getContentText());
  var out = (body_.content || []).filter(function (c) { return c.type === 'text'; })
    .map(function (c) { return c.text; }).join('').trim();
  var data = parseReviewerJson_(out);
  if (!data) throw new Error('Reviewer did not return JSON: ' + out.slice(0, 200));
  return {
    ok: true,
    draft: {
      verdict: data.verdict || '',
      confidence: typeof data.confidence === 'number' ? data.confidence : '',
      violationType: data.violation_type || '',
      violationReason: data.violation_reason || '',
      extractedText: data.extracted_text || text,
      productName: data.product_name || '',
      deskripsi: data.complaint_description_bm || '',
      notes: data.notes || ''
    }
  };
}

// The model is asked for bare JSON; a code fence or a stray sentence around it
// still parses rather than failing the whole draft.
function parseReviewerJson_(out) {
  var t = String(out || '').replace(/^```(?:json)?\s*/i, '').replace(/```\s*$/, '').trim();
  try { return JSON.parse(t); } catch (err) { /* fall through to the brace scan */ }
  var start = t.indexOf('{'), end = t.lastIndexOf('}');
  if (start < 0 || end <= start) return null;
  try { return JSON.parse(t.slice(start, end + 1)); } catch (err) { return null; }
}

function openRecord_(id) {
  var row = getRecord_(id);
  if (!row) throw new Error('Not found: ' + id);
  if (row['Status'] === 'New') {
    updateStatus_(id, 'In-Progress');
    row = getRecord_(id);
  }
  return row;
}

var EDITABLE_FIELDS = ['Remarks', 'Nama Kosmetik', 'Nombor Notifikasi', 'Jenis Aduan', 'Deskripsi Aduan',
                       'KKM Feedback', 'Violation Type', 'Violation Reason', 'Brand', 'Platform',
                       'Screenshot Link', 'Extracted Text'];

function updateFields_(id, fields) {
  return withLock_(function () {
    var sheet = sheet_();
    var r = findRowById_(sheet, id);
    if (r < 0) throw new Error('Not found: ' + id);
    var rowVals = sheet.getRange(r, 1, 1, HEADERS.length).getValues()[0];
    Object.keys(fields || {}).forEach(function (k) {
      if (EDITABLE_FIELDS.indexOf(k) >= 0) rowVals[COL[k]] = fields[k] == null ? '' : String(fields[k]);
    });
    closeOnFeedback_(rowVals);
    rowVals[COL['Updated At']] = nowIso_();
    sheet.getRange(r, 1, 1, HEADERS.length).setValues([rowVals]);
    return { ok: true, row: rowToObject_(rowVals) };
  });
}

function closeOnFeedback_(rowVals) {
  // KKM has replied, so the complaint is finished: mark it Complete and, if the submission
  // date was never filled in by hand, stamp it - a reply cannot arrive before a submission.
  // Dismissed is left alone: a row we chose not to file is not completed by someone's answer.
  if (!isArchived_(rowVals)) return false;
  if (rowVals[COL['Status']] === 'Dismissed') return false;
  var changed = false;
  if (rowVals[COL['Status']] !== 'Complete') { rowVals[COL['Status']] = 'Complete'; changed = true; }
  if (!rowVals[COL['Tarikh Melapor']]) {
    rowVals[COL['Tarikh Melapor']] = Utilities.formatDate(new Date(), TZ, 'yyyy-MM-dd');
    changed = true;
  }
  return changed;
}

function insertManual_(record) {
  if (!record || !record['Brand'] || !record['Platform'] || !record['Post URL']) {
    throw new Error('Brand, Platform and Post URL are required.');
  }
  var res = apiInsertRecords_([record], 'manual');
  if (!res.ok) throw new Error(res.error || 'Insert failed');
  if (res.inserted === 0) throw new Error('That Post URL is already in the database (' + (res.duplicates[0] || '') + ').');
  return { ok: true, row: getRecord_(res.ids[0]), stats: stats_() };
}

// ---------------------------------------------------------------------------
// UI RPC (google.script.run). Every call carries the dashboard key.
// ---------------------------------------------------------------------------
function apiBootstrap(key) {
  requireViewer_(key);
  return {
    rows: listRecords_(),
    stats: stats_(),
    lookups: lookups_(),
    kkmFormUrl: PROPS.getProperty('KKM_FORM_URL') || '',
    questUrl: 'https://quest3plus.bpfk.gov.my/pmo2/index.php',
    statuses: STATUSES,
    routes: routesPayload_(),
    summary: summary_()
  };
}

function apiList(key, filters) {
  requireViewer_(key);
  filters = filters || {};
  return { rows: listRecords_(filters.status, filters.brand, filters.platform, filters.q), stats: stats_() };
}

function apiOpen(key, id) {
  // Called when a row is opened in the dashboard: New -> In-Progress automatically.
  requireViewer_(key);
  return openRecord_(id);
}

function apiSetStatus(key, id, status, remarks) {
  requireViewer_(key);
  return updateStatus_(id, status, remarks);
}

function apiAdvanceStatus(key, id) {
  requireViewer_(key);
  var row = getRecord_(id);
  if (!row) throw new Error('Not found: ' + id);
  return updateStatus_(id, STATUS_FLOW[row['Status']] || 'In-Progress');
}

function apiUpdateFields(key, id, fields) {
  requireViewer_(key);
  return updateFields_(id, fields);
}

function apiCreateManual(key, record) {
  requireViewer_(key);
  return insertManual_(record);
}

function apiUploadScreenshot(key, base64, mimeType, filename) {
  requireViewer_(key);
  return { ok: true, link: saveScreenshot_(base64, mimeType, filename) };
}

function apiSetKkmFormUrl(key, url) {
  requireViewer_(key);
  PROPS.setProperty('KKM_FORM_URL', String(url || ''));
  return { ok: true };
}

// ---------------------------------------------------------------------------
// Core data operations
// ---------------------------------------------------------------------------
function apiInsertRecords_(records, source) {
  if (!Array.isArray(records) || records.length === 0) return { ok: false, error: 'No records supplied' };
  return withLock_(function () {
    var sheet = sheet_();
    ensureHeaders_(sheet);
    var known = knownUrlSet_(sheet);
    var toAppend = [], ids = [], duplicates = [], errors = [];
    var today = Utilities.formatDate(new Date(), TZ, 'yyyy-MM-dd');
    records.forEach(function (rec, i) {
      try {
        rec = normaliseRecord_(rec);
        if (!rec['Post URL']) { errors.push({ index: i, error: 'Post URL missing' }); return; }
        var urlKey = canonicalUrl_(rec['Post URL']);
        if (known[urlKey]) { duplicates.push(rec['Post URL']); return; }
        known[urlKey] = true;
        var link = rec['Screenshot Link'] || '';
        if (!link && rec.screenshot_base64) {
          link = saveScreenshot_(rec.screenshot_base64, rec.screenshot_mime || 'image/jpeg',
            safeFilename_(rec['Brand'] + '_' + rec['Platform'] + '_' + today + '_' + (i + 1)) + extFor_(rec.screenshot_mime));
        }
        var id = makeId_();
        var row = new Array(HEADERS.length).fill('');
        row[COL['ID']] = id;
        row[COL['Date']] = rec['Date'] || today;
        row[COL['Brand']] = rec['Brand'];
        row[COL['Platform']] = rec['Platform'];
        row[COL['Post URL']] = rec['Post URL'];
        row[COL['Screenshot Link']] = link;
        row[COL['Extracted Text']] = rec['Extracted Text'];
        row[COL['Violation Type']] = rec['Violation Type'];
        row[COL['Violation Reason']] = rec['Violation Reason'];
        row[COL['Status']] = 'New';
        row[COL['Remarks']] = rec['Remarks'];
        row[COL['Nama Kosmetik']] = rec['Nama Kosmetik'];
        row[COL['Nombor Notifikasi']] = rec['Nombor Notifikasi'];
        row[COL['Jenis Aduan']] = rec['Jenis Aduan'] || 'Iklan Kosmetik';
        row[COL['Deskripsi Aduan']] = rec['Deskripsi Aduan'] || buildComplaintDescription_(rec);
        row[COL['Tarikh Melapor']] = '';
        row[COL['KKM Feedback']] = rec['KKM Feedback'];
        row[COL['Source']] = rec['Source'] || source || 'scraper';
        row[COL['Confidence']] = rec['Confidence'];
        row[COL['Created At']] = nowIso_();
        row[COL['Updated At']] = nowIso_();
        toAppend.push(row); ids.push(id);
      } catch (err) {
        errors.push({ index: i, error: String(err && err.message || err) });
      }
    });
    if (toAppend.length) {
      sheet.getRange(sheet.getLastRow() + 1, 1, toAppend.length, HEADERS.length).setValues(toAppend);
      SpreadsheetApp.flush();
    }
    return { ok: true, inserted: toAppend.length, ids: ids, duplicates: duplicates, errors: errors };
  });
}

function normaliseRecord_(rec) {
  // Accept both Sheet-header keys ("Post URL") and snake_case keys (post_url).
  var map = {
    date: 'Date', brand: 'Brand', platform: 'Platform', post_url: 'Post URL', url: 'Post URL',
    screenshot_link: 'Screenshot Link', screenshot_url: 'Screenshot Link', extracted_text: 'Extracted Text',
    text: 'Extracted Text', violation_type: 'Violation Type', violation_reason: 'Violation Reason',
    reason: 'Violation Reason', remarks: 'Remarks', product_name: 'Nama Kosmetik', nama_kosmetik: 'Nama Kosmetik',
    notification_number: 'Nombor Notifikasi', nombor_notifikasi: 'Nombor Notifikasi', jenis_aduan: 'Jenis Aduan',
    complaint_type: 'Jenis Aduan', deskripsi_aduan: 'Deskripsi Aduan', complaint_description: 'Deskripsi Aduan',
    source: 'Source', confidence: 'Confidence', kkm_feedback: 'KKM Feedback', feedback: 'KKM Feedback'
  };
  var out = { screenshot_base64: rec.screenshot_base64 || '', screenshot_mime: rec.screenshot_mime || '' };
  HEADERS.forEach(function (h) { out[h] = rec[h] != null ? rec[h] : ''; });
  Object.keys(map).forEach(function (k) {
    if (rec[k] != null && rec[k] !== '' && !out[map[k]]) out[map[k]] = rec[k];
  });
  ['Brand', 'Platform', 'Post URL', 'Extracted Text', 'Violation Type', 'Violation Reason', 'Remarks',
   'Nama Kosmetik', 'Nombor Notifikasi', 'Jenis Aduan', 'Deskripsi Aduan', 'KKM Feedback', 'Source'].forEach(function (h) {
    out[h] = out[h] == null ? '' : String(out[h]).trim();
  });
  if (out['Extracted Text'].length > 45000) out['Extracted Text'] = out['Extracted Text'].slice(0, 45000) + ' …';
  if (out['Confidence'] !== '' && out['Confidence'] != null) {
    var c = Number(out['Confidence']); out['Confidence'] = isNaN(c) ? '' : Math.round(c * 100) / 100;
  }
  if (out['Date']) {
    var d = new Date(out['Date']);
    out['Date'] = isNaN(d.getTime()) ? '' : Utilities.formatDate(d, TZ, 'yyyy-MM-dd');
  }
  if (out['Jenis Aduan'] && JENIS_ADUAN.indexOf(out['Jenis Aduan']) < 0) out['Jenis Aduan'] = 'Iklan Kosmetik';
  return out;
}

// Fallback complaint text (BM) when the client did not supply one. Nothing here is a fact the
// scraper cannot vouch for: it only restates what was captured, and the legal basis is the one
// sentence the route carries, so a food complaint cites the Food Act and not Annex I.
// The dashboard has the same function (routeDescription) for "rewrite for this route".
function buildComplaintDescription_(rec) {
  var route = routeOf_(rec['Jenis Aduan']);
  var product = rec['Nama Kosmetik'] || '[SAHKAN: nama produk]';
  var refLabel = (route.ref && route.ref.label) || 'Nombor rujukan';
  // A cosmetic always has a NOT to check; another route's number is only demanded when the route
  // says what it looks like (ref.pattern), otherwise "tiada" is an honest answer and not a gap.
  var ref = rec['Nombor Notifikasi'] || (route.kkm ? '[SAHKAN: semak QUEST3+]' : (route.ref && route.ref.pattern ? '[SAHKAN: ' + refLabel + ']' : 'tiada'));
  var heading = route.kkm
    ? (route.value === 'Kualiti Kosmetik' ? 'Aduan kualiti produk kosmetik bernotifikasi.' : 'Aduan iklan kosmetik bernotifikasi.')
    : 'Aduan ' + route.value.replace(/\s*\(.*\)\s*$/, '').toLowerCase() + ' — untuk perhatian ' + (route.agency.split(' — ')[0] || route.agency) + '.';
  var lines = [
    heading,
    'Jenama: ' + (rec['Brand'] || '-') + '. Produk: ' + product + '. Platform: ' + (rec['Platform'] || '-') + '.',
    'Pautan iklan: ' + (rec['Post URL'] || '-'),
    'Dakwaan yang dikesan: ' + (rec['Violation Reason'] || '-'),
    'Jenis pelanggaran: ' + (rec['Violation Type'] || '-') + '.'
  ];
  if (route.basis) lines.push(route.basis);
  lines.push('Tangkapan skrin dilampirkan. ' + refLabel + ': ' + ref + '.');
  return lines.join('\n');
}

function updateStatus_(id, status, remarks) {
  if (STATUSES.indexOf(status) < 0) return { ok: false, error: 'Invalid status. Use one of: ' + STATUSES.join(', ') };
  return withLock_(function () {
    var sheet = sheet_();
    var r = findRowById_(sheet, id);
    if (r < 0) return { ok: false, error: 'Not found: ' + id };
    var rowVals = sheet.getRange(r, 1, 1, HEADERS.length).getValues()[0];
    rowVals[COL['Status']] = status;
    if (remarks != null && remarks !== '') rowVals[COL['Remarks']] = String(remarks);
    if (status === 'Complete' && !rowVals[COL['Tarikh Melapor']]) {
      rowVals[COL['Tarikh Melapor']] = Utilities.formatDate(new Date(), TZ, 'yyyy-MM-dd');
    }
    rowVals[COL['Updated At']] = nowIso_();
    sheet.getRange(r, 1, 1, HEADERS.length).setValues([rowVals]);
    return { ok: true, row: rowToObject_(rowVals), stats: stats_() };
  });
}

function listRecords_(status, brand, platform, q) {
  var sheet = sheet_();
  var last = sheet.getLastRow();
  if (last < 2) return [];
  var vals = sheet.getRange(2, 1, last - 1, HEADERS.length).getValues();
  var query = String(q || '').trim().toLowerCase();
  var out = [];
  for (var i = vals.length - 1; i >= 0; i--) { // newest first
    var row = vals[i];
    if (!row[COL['ID']]) continue;
    // A search reaches everywhere, archive included: not finding a complaint you know you
    // filed is worse than a longer list. Without one, closed files stay out of the way.
    if (query) { if (!rowMatches_(row, query)) continue; }
    else if (status === ARCHIVED_FILTER) { if (!isArchived_(row)) continue; }
    else if (isArchived_(row)) continue;
    else if (status && row[COL['Status']] !== status) continue;
    if (brand && row[COL['Brand']] !== brand) continue;
    if (platform && row[COL['Platform']] !== platform) continue;
    // A search hit is opened and read, so it is worth its full text; a listed row is not.
    out.push(query ? rowToObject_(row) : listRowToObject_(row));
    if (out.length >= MAX_ROWS_RETURNED) break;
  }
  return out;
}

var SEARCH_FIELDS = ['Extracted Text', 'Nama Kosmetik', 'Violation Reason', 'Violation Type',
                     'Post URL', 'Remarks', 'ID', 'KKM Feedback', 'Brand', 'Nombor Notifikasi'];

function rowMatches_(row, query) {
  for (var i = 0; i < SEARCH_FIELDS.length; i++) {
    var v = row[COL[SEARCH_FIELDS[i]]];
    if (v && String(v).toLowerCase().indexOf(query) >= 0) return true;
  }
  return false;
}

function getRecord_(id) {
  var sheet = sheet_();
  var r = findRowById_(sheet, id);
  if (r < 0) return null;
  return rowToObject_(sheet.getRange(r, 1, 1, HEADERS.length).getValues()[0]);
}

function stats_() {
  var counts = { total: 0 }; STATUSES.forEach(function (s) { counts[s] = 0; });
  counts[ARCHIVED_FILTER] = 0;
  var byBrand = {}, byPlatform = {};
  var sheet = sheet_();
  var last = sheet.getLastRow();
  if (last >= 2) {
    var vals = sheet.getRange(2, 1, last - 1, HEADERS.length).getValues();
    vals.forEach(function (row) {
      if (!row[COL['ID']]) return;
      counts.total++;
      if (isArchived_(row)) { counts[ARCHIVED_FILTER]++; }
      else { var s = row[COL['Status']]; counts[s] = (counts[s] || 0) + 1; }
      var b = row[COL['Brand']]; byBrand[b] = (byBrand[b] || 0) + 1;
      var p = row[COL['Platform']]; byPlatform[p] = (byPlatform[p] || 0) + 1;
    });
  }
  return { counts: counts, byBrand: byBrand, byPlatform: byPlatform, generatedAt: nowIso_() };
}

// ---------------------------------------------------------------------------
// Overview figures for the dashboard's home view (Wan, 9 Oct 2026: "add home dashboard to see
// overall status"). Computed here over EVERY row, because the list the page holds is capped at
// MAX_ROWS_RETURNED and leaves the archive out - a chart drawn from it would quietly undercount.
// One pass over the sheet; nothing is written.
// ---------------------------------------------------------------------------
function summary_() {
  var sheet = sheet_();
  var last = sheet.getLastRow();
  var byMonth = {}, byRoute = {}, byPlatform = {}, byBrand = {}, byType = {}, bySource = {}, byStatus = { total: 0 };
  STATUSES.forEach(function (s) { byStatus[s] = 0; }); byStatus[ARCHIVED_FILTER] = 0;
  var filed = 0, replied = 0, lags = [], last7 = 0, last30 = 0, oldestNew = null, newest = '';
  var now = new Date(), day = 86400000;
  JENIS_ADUAN.forEach(function (v) { byRoute[v] = { total: 0, filed: 0, replied: 0, open: 0 }; });
  if (last >= 2) {
    var vals = sheet.getRange(2, 1, last - 1, HEADERS.length).getValues();
    vals.forEach(function (row) {
      if (!row[COL['ID']]) return;
      var date = row[COL['Date']] instanceof Date ? Utilities.formatDate(row[COL['Date']], TZ, 'yyyy-MM-dd') : String(row[COL['Date']] || '');
      var month = date.slice(0, 7) || 'unknown';
      var archived = isArchived_(row);
      var status = archived ? ARCHIVED_FILTER : String(row[COL['Status']] || 'New');
      byStatus.total++; byStatus[status] = (byStatus[status] || 0) + 1;
      var m = byMonth[month] || (byMonth[month] = { total: 0, 'New': 0, 'In-Progress': 0, 'Complete': 0, 'Dismissed': 0, 'Archived': 0 });
      m.total++; m[status] = (m[status] || 0) + 1;
      var route = String(row[COL['Jenis Aduan']] || 'Iklan Kosmetik');
      var rr = byRoute[route] || (byRoute[route] = { total: 0, filed: 0, replied: 0, open: 0 });
      rr.total++;
      var tarikh = row[COL['Tarikh Melapor']] instanceof Date ? Utilities.formatDate(row[COL['Tarikh Melapor']], TZ, 'yyyy-MM-dd') : String(row[COL['Tarikh Melapor']] || '');
      if (tarikh) {
        filed++; rr.filed++;
        var d0 = new Date(date), d1 = new Date(tarikh);
        if (!isNaN(d0.getTime()) && !isNaN(d1.getTime())) lags.push(Math.max(0, Math.round((d1 - d0) / day)));
      }
      if (archived) { replied++; rr.replied++; }
      if (status === 'New' || status === 'In-Progress') rr.open++;
      var p = String(row[COL['Platform']] || '-'); byPlatform[p] = (byPlatform[p] || 0) + 1;
      var b = String(row[COL['Brand']] || '-'); byBrand[b] = (byBrand[b] || 0) + 1;
      var t = String(row[COL['Violation Type']] || '-'); byType[t] = (byType[t] || 0) + 1;
      var src = String(row[COL['Source']] || '-'); bySource[src] = (bySource[src] || 0) + 1;
      var created = String(row[COL['Created At']] || '');
      var cd = new Date(created || date);
      if (!isNaN(cd.getTime())) {
        var age = (now - cd) / day;
        if (age <= 7) last7++;
        if (age <= 30) last30++;
        if (status === 'New' && (oldestNew === null || cd < oldestNew)) oldestNew = cd;
      }
      if (created > newest) newest = created;
    });
  }
  lags.sort(function (a, b) { return a - b; });
  var median = lags.length ? (lags.length % 2 ? lags[(lags.length - 1) / 2] : Math.round((lags[lags.length / 2 - 1] + lags[lags.length / 2]) / 2)) : null;
  return {
    byStatus: byStatus, byMonth: byMonth, byRoute: byRoute, byPlatform: byPlatform, byBrand: byBrand, byType: byType, bySource: bySource,
    filed: filed, replied: replied, lagMedianDays: median, lagCount: lags.length, last7: last7, last30: last30,
    oldestNewDays: oldestNew ? Math.round((now - oldestNew) / day) : null, newestCreatedAt: newest, generatedAt: nowIso_()
  };
}

function lookups_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(LOOKUP_SHEET_NAME);
  if (!sheet) return { brands: DEFAULT_BRANDS, platforms: DEFAULT_PLATFORMS, violationTypes: DEFAULT_VIOLATION_TYPES, jenisAduan: JENIS_ADUAN };
  function colList(c) {
    var last = sheet.getLastRow();
    if (last < 2) return [];
    return sheet.getRange(2, c, last - 1, 1).getValues().map(function (r) { return String(r[0]).trim(); }).filter(String);
  }
  var brands = colList(1).length ? colList(1) : DEFAULT_BRANDS.slice();
  try {
    targets_().forEach(function (t) { if (brands.indexOf(t.name) < 0) brands.push(t.name); });
  } catch (err) { console.warn('targets unavailable for lookups: ' + err); }
  return {
    brands: brands,
    platforms: colList(2).length ? colList(2) : DEFAULT_PLATFORMS,
    violationTypes: colList(3).length ? colList(3) : DEFAULT_VIOLATION_TYPES,
    jenisAduan: JENIS_ADUAN
  };
}

function knownUrls_() {
  return Object.keys(knownUrlSet_(sheet_()));
}

// ---------------------------------------------------------------------------
// Exports (Wan, 9 Oct 2026: "add features can export to docs, pdf, csv when needed").
//
// `export` hands the dashboard FULL rows (no truncation), filtered the same way the list is,
// in pages of up to EXPORT_PAGE_MAX so a 2,000-row register never has to fit one response.
// `all: true` includes the archive. The dashboard turns the pages into a CSV in the browser.
//
// `export_doc` builds a Google Doc in Drive ("KKM Complaint Exports") and returns its link;
// with format 'pdf' or 'docx' it also returns the file's bytes (base64) so the browser can
// save it. Two shapes: a REGISTER (one table row per complaint) and a DOSSIER (one complaint,
// every field, the screenshot embedded when Drive lets us read it) - the dossier is what goes
// to an agency with a submission. The Doc stays in Drive, so there is a history of what was
// sent and when.
// ---------------------------------------------------------------------------
function selectRows_(f) {
  f = f || {};
  var sheet = sheet_();
  var last = sheet.getLastRow();
  if (last < 2) return [];
  var vals = sheet.getRange(2, 1, last - 1, HEADERS.length).getValues();
  var ids = Array.isArray(f.ids) && f.ids.length ? f.ids.reduce(function (m, id) { m[String(id)] = true; return m; }, {}) : null;
  var query = String(f.q || '').trim().toLowerCase();
  var out = [];
  for (var i = vals.length - 1; i >= 0; i--) {
    var row = vals[i];
    if (!row[COL['ID']]) continue;
    if (ids) { if (!ids[String(row[COL['ID']])]) continue; }
    else {
      var archived = isArchived_(row);
      if (f.status === ARCHIVED_FILTER) { if (!archived) continue; }
      else if (f.status === 'Complete') { if (!(row[COL['Status']] === 'Complete')) continue; }   // Complete means filed, replied or not
      else if (f.status) { if (archived || row[COL['Status']] !== f.status) continue; }
      else if (!f.all && archived) continue;
      if (f.brand && row[COL['Brand']] !== f.brand) continue;
      if (f.platform && row[COL['Platform']] !== f.platform) continue;
      if (f.route && String(row[COL['Jenis Aduan']] || 'Iklan Kosmetik') !== f.route) continue;
      var date = row[COL['Date']] instanceof Date ? Utilities.formatDate(row[COL['Date']], TZ, 'yyyy-MM-dd') : String(row[COL['Date']] || '');
      if (f.from && date && date < String(f.from)) continue;
      if (f.to && date && date > String(f.to)) continue;
      if (query && !rowMatches_(row, query)) continue;
    }
    out.push(rowToObject_(row));
  }
  return out;
}

function exportRows_(body) {
  var all = selectRows_(body);
  var offset = Math.max(0, Number(body.offset) || 0);
  var limit = Math.min(EXPORT_PAGE_MAX, Math.max(1, Number(body.limit) || EXPORT_PAGE_MAX));
  return { ok: true, total: all.length, offset: offset, rows: all.slice(offset, offset + limit), headers: HEADERS, generatedAt: nowIso_() };
}

function driveIdFromLink_(link) {
  var m = /drive\.google\.com\/file\/d\/([^/?#]+)/.exec(link || '') || /[?&]id=([^&]+)/.exec(link || '');
  return m ? m[1] : '';
}

function exportDoc_(body) {
  body = body || {};
  var kind = body.kind === 'dossier' ? 'dossier' : 'register';
  var format = ['pdf', 'docx', 'gdoc'].indexOf(body.format) >= 0 ? body.format : 'gdoc';
  var rows = selectRows_(body);
  if (!rows.length) throw new Error('Nothing to export: no complaint matches.');
  if (kind === 'register' && rows.length > EXPORT_DOC_MAX_ROWS) rows = rows.slice(0, EXPORT_DOC_MAX_ROWS);
  var stamp = Utilities.formatDate(new Date(), TZ, 'yyyy-MM-dd HHmm');
  var title = String(body.title || '').trim() ||
    (kind === 'dossier' ? 'Aduan ' + rows[0]['ID'] + ' - ' + (rows[0]['Brand'] || '') : 'Daftar Aduan KKM - ' + stamp);
  var doc = DocumentApp.create(title);
  var docBody = doc.getBody();
  docBody.setMarginTop(50).setMarginBottom(50).setMarginLeft(54).setMarginRight(54);
  if (kind === 'dossier') {
    rows.forEach(function (r, i) { if (i) docBody.appendPageBreak(); writeDossier_(docBody, r, body.includeScreenshot !== false && i < EXPORT_DOC_MAX_IMAGES); });
  } else {
    writeRegister_(docBody, rows, body);
  }
  doc.saveAndClose();
  var file = DriveApp.getFileById(doc.getId());
  try { ensureExportFolder_().addFile(file); DriveApp.getRootFolder().removeFile(file); } catch (err) { console.warn('export folder: ' + err); }
  var out = { ok: true, url: doc.getUrl(), id: doc.getId(), name: title, count: rows.length, format: format };
  if (format === 'pdf') {
    var pdf = file.getAs('application/pdf');
    out.base64 = Utilities.base64Encode(pdf.getBytes()); out.mime = 'application/pdf'; out.filename = safeFilename_(title) + '.pdf';
  } else if (format === 'docx') {
    var res = UrlFetchApp.fetch('https://docs.google.com/feeds/download/documents/export/Export?id=' + doc.getId() + '&exportFormat=docx',
      { headers: { Authorization: 'Bearer ' + ScriptApp.getOAuthToken() }, muteHttpExceptions: true });
    if (res.getResponseCode() !== 200) throw new Error('Word export failed (HTTP ' + res.getResponseCode() + '). The Google Doc is saved: ' + doc.getUrl());
    out.base64 = Utilities.base64Encode(res.getContent()); out.mime = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'; out.filename = safeFilename_(title) + '.docx';
  }
  return out;
}

function writeRegister_(docBody, rows, f) {
  var h = docBody.appendParagraph('Daftar Aduan — KKM Complaint System'); h.setHeading(DocumentApp.ParagraphHeading.HEADING1);
  var filters = [];
  if (f.status) filters.push('Status: ' + f.status); if (f.brand) filters.push('Brand: ' + f.brand); if (f.platform) filters.push('Platform: ' + f.platform);
  if (f.route) filters.push('Laluan: ' + f.route); if (f.from || f.to) filters.push('Tarikh: ' + (f.from || '…') + ' hingga ' + (f.to || '…')); if (f.q) filters.push('Carian: "' + f.q + '"');
  docBody.appendParagraph('Dijana ' + nowIso_() + ' · ' + rows.length + ' aduan' + (filters.length ? ' · ' + filters.join(' · ') : '')).setFontSize(9).setForegroundColor('#64748b');
  // Status roll-up above the table so the first page reads as a report, not a dump.
  var counts = {};
  rows.forEach(function (r) { var s = r['KKM Feedback'] ? 'Dijawab (archived)' : r['Status']; counts[s] = (counts[s] || 0) + 1; });
  docBody.appendParagraph(Object.keys(counts).map(function (k) { return k + ': ' + counts[k]; }).join('   ·   ')).setFontSize(10);
  var cols = ['ID', 'Date', 'Brand', 'Platform', 'Nama Kosmetik', 'Violation Type', 'Jenis Aduan', 'Status', 'Tarikh Melapor', 'Post URL'];
  var data = [cols].concat(rows.map(function (r) { return cols.map(function (c) { return String(r[c] == null ? '' : r[c]); }); }));
  var table = docBody.appendTable(data);
  table.setBorderWidth(0.5).setBorderColor('#cbd5e1');
  var widths = [92, 56, 80, 56, 90, 100, 90, 56, 60, 120];
  for (var c = 0; c < cols.length; c++) table.setColumnWidth(c, widths[c]);
  for (var r = 0; r < table.getNumRows(); r++) {
    var row = table.getRow(r);
    for (var cc = 0; cc < row.getNumCells(); cc++) {
      var cell = row.getCell(cc); cell.setFontSize(7.5).setPaddingTop(2).setPaddingBottom(2).setPaddingLeft(3).setPaddingRight(3);
      if (r === 0) { cell.setBackgroundColor('#0f172a'); cell.editAsText().setForegroundColor('#ffffff').setBold(true); }
    }
  }
  docBody.appendParagraph('').setFontSize(6);
  docBody.appendParagraph('Setiap baris ialah saringan pertama oleh sistem; keputusan untuk memfailkan adalah keputusan penilai. Pautan skrin dan teks penuh ada dalam pangkalan data.').setFontSize(8).setForegroundColor('#64748b');
}

function writeDossier_(docBody, r, withShot) {
  var route = routeOf_(r['Jenis Aduan']);
  var h = docBody.appendParagraph('Aduan ' + r['ID']); h.setHeading(DocumentApp.ParagraphHeading.HEADING1);
  docBody.appendParagraph((r['Brand'] || '-') + ' · ' + (r['Platform'] || '-') + ' · dikesan ' + (r['Date'] || '-') + ' · status ' + (r['Status'] || '-')).setFontSize(10).setForegroundColor('#475569');
  var sec = function (t) { docBody.appendParagraph(t).setHeading(DocumentApp.ParagraphHeading.HEADING3); };
  var kv = function (pairs) {
    var t = docBody.appendTable(pairs.map(function (p) { return [p[0], String(p[1] == null || p[1] === '' ? '-' : p[1])]; }));
    t.setBorderWidth(0.5).setBorderColor('#e2e8f0'); t.setColumnWidth(0, 150); t.setColumnWidth(1, 330);
    for (var i = 0; i < t.getNumRows(); i++) { var row = t.getRow(i); row.getCell(0).setBackgroundColor('#f8fafc').editAsText().setBold(true).setFontSize(9); row.getCell(1).editAsText().setFontSize(9); }
  };
  sec('Laluan aduan');
  kv([['Jenis Aduan', r['Jenis Aduan'] || 'Iklan Kosmetik'], ['Agensi', route.agency], ['Instrumen', route.act || '-'],
      ['Saluran', (route.channel && (route.channel.url || route.channel.label)) || '-']]);
  sec('Produk dan iklan');
  kv([['Nama produk', r['Nama Kosmetik']], [(route.ref && route.ref.label) || 'Nombor rujukan', r['Nombor Notifikasi']], ['Jenama / akaun', r['Brand']],
      ['Platform', r['Platform']], ['Pautan iklan', r['Post URL']], ['Tarikh dikesan', r['Date']], ['Sumber', r['Source']],
      ['Keyakinan penyemak', r['Confidence'] !== '' && r['Confidence'] != null ? Math.round(Number(r['Confidence']) * 100) + '%' : '-']]);
  sec('Pelanggaran');
  kv([['Jenis pelanggaran', r['Violation Type']], ['Alasan (dengan petikan)', r['Violation Reason']]]);
  sec('Deskripsi Aduan');
  docBody.appendParagraph(r['Deskripsi Aduan'] || '-').setFontSize(10);
  sec('Teks yang diekstrak');
  docBody.appendParagraph(String(r['Extracted Text'] || '-').slice(0, 6000)).setFontSize(8.5).setForegroundColor('#334155');
  sec('Pemfailan');
  kv([['Tarikh Melapor', r['Tarikh Melapor']], ['Maklum balas agensi', r['KKM Feedback']], ['Catatan', r['Remarks']], ['Dikemas kini', r['Updated At']]]);
  sec('Tangkapan skrin');
  var link = r['Screenshot Link'] || '';
  if (link) docBody.appendParagraph(link).setFontSize(8).setLinkUrl(link);
  if (withShot && link) {
    try {
      var fid = driveIdFromLink_(link);
      if (fid) {
        var blob = DriveApp.getFileById(fid).getBlob();
        if (/^image\//.test(blob.getContentType()) && blob.getBytes().length < 6 * 1024 * 1024) {
          var img = docBody.appendImage(blob);
          var w = img.getWidth(), hh = img.getHeight(), maxW = 440, maxH = 520;
          var k = Math.min(maxW / w, maxH / hh, 1);
          img.setWidth(Math.round(w * k)); img.setHeight(Math.round(hh * k));
        }
      }
    } catch (err) { docBody.appendParagraph('(tangkapan skrin tidak dapat dibaca dari Drive: ' + String(err && err.message || err).slice(0, 120) + ')').setFontSize(8).setForegroundColor('#b91c1c'); }
  } else if (!link) docBody.appendParagraph('Tiada tangkapan skrin pada rekod ini.').setFontSize(9);
}

// ---------------------------------------------------------------------------
// Screenshot storage (Drive)
// ---------------------------------------------------------------------------
function saveScreenshot_(base64, mimeType, filename) {
  if (!base64) return '';
  var folder = ensureFolder_();
  var data = base64.indexOf('base64,') >= 0 ? base64.split('base64,')[1] : base64;
  var bytes = Utilities.base64Decode(data);
  var blob = Utilities.newBlob(bytes, mimeType || 'image/jpeg', filename || ('screenshot_' + Date.now() + '.jpg'));
  var file = folder.createFile(blob);
  try {
    file.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);
  } catch (err) {
    // Workspace domains can forbid link sharing; the owner can still open the file.
    console.warn('Sharing not applied: ' + err);
  }
  return 'https://drive.google.com/file/d/' + file.getId() + '/view';
}

// ---------------------------------------------------------------------------
// Auth helpers
// ---------------------------------------------------------------------------
function checkToken_(token) {
  var expected = PROPS.getProperty('API_TOKEN');
  return !!expected && !!token && constantTimeEquals_(String(token), expected);
}

function isAuthorisedViewer_(key) {
  var expected = PROPS.getProperty('DASHBOARD_KEY');
  if (expected && key && constantTimeEquals_(String(key), expected)) return true;
  if (isAdminEmail_()) return true;
  return false;
}

function isAdminEmail_() {
  try {
    var email = Session.getActiveUser().getEmail();
    var owner = PROPS.getProperty('OWNER_EMAIL');
    return !!(email && owner && email.toLowerCase() === owner.toLowerCase());
  } catch (err) {
    return false; // anonymous / identity not disclosed
  }
}

// The guide is behind a passcode: GUIDE_PASSCODE when set, otherwise DASHBOARD_KEY.
// A web app deployed "Execute as Me / Access Anyone" is never told who is viewing
// (Session.getActiveUser() is empty), so a Google-account check cannot gate it.
// Signed in as the owner still bypasses the prompt where the identity IS available.
function guidePasscode_() {
  return PROPS.getProperty('GUIDE_PASSCODE') || PROPS.getProperty('DASHBOARD_KEY') || '';
}

function serveGuide_(p) {
  p = p || {};
  var expected = guidePasscode_();
  var supplied = String(p.pass || '');
  var ok = isAdminEmail_() || (!!expected && !!supplied && constantTimeEquals_(supplied, expected));
  if (!ok) return guideLockPage_(!!supplied, !expected);

  var t = HtmlService.createTemplateFromFile('Guide');
  t.dashboardUrl = ScriptApp.getService().getUrl();
  t.passcodeJson = JSON.stringify(supplied);   // the page remembers it for this browser
  return t.evaluate()
    .setTitle('KKM Operating Guide')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1')
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

// The prompt. A passcode already stored in this browser is filled in and submitted
// automatically, so it is typed once; a wrong one comes back here with a message.
function guideLockPage_(wrong, unset) {
  var url = ScriptApp.getService().getUrl();
  var msg = unset
    ? '<p style="color:#b91c1c">No passcode is set. Add <code>GUIDE_PASSCODE</code> in Apps Script → Project Settings → Script properties.</p>'
    : (wrong ? '<p style="color:#b91c1c">Wrong passcode.</p>' : '');
  return HtmlService.createHtmlOutput(
    '<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">' +
    '<style>body{font-family:Inter,system-ui,sans-serif;background:#f8fafc;color:#0f172a;margin:0;' +
    'display:grid;place-items:center;min-height:100vh}' +
    '.card{background:#fff;border:1px solid #e2e8f0;border-radius:16px;padding:28px;max-width:380px;width:90%;' +
    'box-shadow:0 10px 30px rgba(15,23,42,.06)}h2{margin:0 0 6px;font-size:18px}' +
    'p{font-size:14px;color:#475569;margin:6px 0 14px}' +
    'input{width:100%;box-sizing:border-box;padding:10px 12px;border:1px solid #cbd5e1;border-radius:10px;font-size:14px}' +
    'button{margin-top:10px;width:100%;padding:10px 12px;border:0;border-radius:10px;background:#0f172a;color:#fff;' +
    'font-size:14px;font-weight:600;cursor:pointer}</style></head><body><div class="card">' +
    '<h2>KKM Operating Guide</h2><p>Enter the passcode to open the guide.</p>' + msg +
    '<form id="f" method="get" action="' + url + '" target="_top">' +
    '<input type="hidden" name="view" value="guide">' +
    '<input id="pass" type="password" name="pass" placeholder="Passcode" autofocus autocomplete="current-password">' +
    '<button type="submit">Open guide</button></form>' +
    '<script>(function(){try{' +
    'var saved=localStorage.getItem("kkm.guidepass");' +
    'var tried=' + (wrong ? 'true' : 'false') + ';' +
    'if(saved&&!tried){document.getElementById("pass").value=saved;document.getElementById("f").submit();}' +
    'if(tried){localStorage.removeItem("kkm.guidepass");}' +
    '}catch(e){}})();<\/script>' +
    '</div></body></html>')
    .setTitle('KKM Guide — locked')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1');
}

function requireViewer_(key) {
  if (!isAuthorisedViewer_(key)) throw new Error('Unauthorised: dashboard key missing or wrong.');
}

function constantTimeEquals_(a, b) {
  if (a.length !== b.length) return false;
  var diff = 0;
  for (var i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------
function sheet_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) { sheet = ss.insertSheet(SHEET_NAME); ensureHeaders_(sheet); }
  return sheet;
}

function findRowById_(sheet, id) {
  var last = sheet.getLastRow();
  if (last < 2 || !id) return -1;
  var ids = sheet.getRange(2, COL['ID'] + 1, last - 1, 1).getValues();
  for (var i = 0; i < ids.length; i++) if (String(ids[i][0]) === String(id)) return i + 2;
  return -1;
}

function rowToObject_(row) {
  var o = {};
  HEADERS.forEach(function (h, i) {
    var v = row[i];
    if (v instanceof Date) v = Utilities.formatDate(v, TZ, 'yyyy-MM-dd');
    o[h] = v == null ? '' : v;
  });
  return o;
}

function withLock_(fn) {
  var lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try { return fn(); } finally { lock.releaseLock(); }
}

function makeId_() {
  return 'KKM-' + Utilities.formatDate(new Date(), TZ, 'yyyyMMdd') + '-' + randomToken_(4).toUpperCase();
}

function randomToken_(n) {
  var chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
  var out = '';
  for (var i = 0; i < n; i++) out += chars.charAt(Math.floor(Math.random() * chars.length));
  return out;
}

function canonicalUrl_(u) {
  u = String(u || '').trim();
  try {
    u = u.replace(/^http:\/\//i, 'https://').replace(/^https:\/\/(m|www|web)\./i, 'https://');
    u = u.split('#')[0];
    // Strip tracking params but keep identity params Facebook relies on (story_fbid, id, v, fbid).
    var parts = u.split('?');
    if (parts.length > 1) {
      var keep = parts[1].split('&').filter(function (kv) {
        return /^(story_fbid|id|v|fbid|set)=/i.test(kv);
      });
      u = parts[0] + (keep.length ? '?' + keep.join('&') : '');
    }
    return u.replace(/\/+$/, '').toLowerCase();
  } catch (err) { return u.toLowerCase(); }
}

function safeFilename_(s) {
  return String(s || 'screenshot').replace(/[^a-z0-9_\-]+/gi, '_').slice(0, 80);
}

function extFor_(mime) {
  if (/png/i.test(mime || '')) return '.png';
  if (/webp/i.test(mime || '')) return '.webp';
  return '.jpg';
}

function nowIso_() {
  return Utilities.formatDate(new Date(), TZ, "yyyy-MM-dd'T'HH:mm:ssXXX");
}

function jsonResponse_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

function include(filename) {
  return HtmlService.createHtmlOutputFromFile(filename).getContent();
}

// ---------------------------------------------------------------------------
// Smoke test — run from the editor after setup() to prove the pipeline end to end.
// ---------------------------------------------------------------------------
function smokeTest_() {
  var res = apiInsertRecords_([{
    brand: 'Test Brand', platform: 'Instagram',
    post_url: 'https://www.instagram.com/p/SMOKE' + Date.now() + '/',
    extracted_text: 'Merawat jerawat secara efektif dalam 3 hari.',
    violation_type: 'Medicinal / disease claim',
    violation_reason: '"Merawat jerawat" = treats acne. Annex I Part 8, Skin products: "Heals, treats or stops acne" is unacceptable.',
    product_name: 'Test Serum', confidence: 0.95
  }], 'scraper');
  Logger.log(JSON.stringify(res));
  Logger.log(JSON.stringify(updateStatus_(res.ids[0], 'In-Progress', 'opened in smoke test')));
  Logger.log(JSON.stringify(stats_()));
  Logger.log(JSON.stringify(summary_()).slice(0, 500));
}
