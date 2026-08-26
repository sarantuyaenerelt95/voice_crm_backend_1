# app/i18n/catalog.py

"""Mongolian translations, keyed by the English source string.

Adding a string to the interface needs nothing here: an untranslated key
renders as its English self. Add an entry when the Mongolian is ready.

Keep keys byte-identical to what the template passes to t(), including
punctuation and capitalisation - the lookup is exact.
"""

from __future__ import annotations


CATALOG: dict[str, dict[str, str]] = {}


def _add(section: str, pairs: dict[str, str]) -> None:
    """Register one group of translations. `section` is documentation only."""
    for english, mongolian in pairs.items():
        CATALOG[english] = {"mn": mongolian, "section": section}


# ---------------------------------------------------------------- navigation

_add("nav", {
    "Dashboard": "Хянах самбар",
    "Campaigns": "Кампанит ажил",
    "Contacts": "Харилцагч",
    "Audio": "Аудио",
    "STT": "Яриа таних",
    "Profile": "Профайл",
    "Logout": "Гарах",
    "Skip to main content": "Үндсэн хэсэг рүү очих",
    "Language": "Хэл",
    "Main": "Үндсэн цэс",
})

# ------------------------------------------------------------ shared actions

_add("actions", {
    "Save": "Хадгалах",
    "Cancel": "Цуцлах",
    "Delete": "Устгах",
    "Edit": "Засах",
    "Show": "Харах",
    "Hide": "Нуух",
    "Download": "Татах",
    "Back": "Буцах",
    "← Back": "← Буцах",
    "Search": "Хайх",
    "Actions": "Үйлдэл",
    "Action": "Үйлдэл",
    "Status": "Төлөв",
    "Name": "Нэр",
    "Email": "И-мэйл",
    "Password": "Нууц үг",
    "Phone": "Утас",
    "Duration": "Үргэлжлэх хугацаа",
    "Created": "Үүсгэсэн",
    "No.": "Д/д",
})

# ------------------------------------------------------------------ sign-in

_add("auth", {
    "Sign in": "Нэвтрэх",
    "Login": "Нэвтрэх",
    "Logout": "Гарах",
    "Create account": "Бүртгүүлэх",
    "Create an account": "Бүртгүүлэх",
    "Create Account": "Бүртгүүлэх",
    "Register": "Бүртгүүлэх",
    "New here?": "Шинэ хэрэглэгч үү?",
    "Already have an account?": "Бүртгэлтэй юу?",
    "Forgot password?": "Нууц үгээ мартсан уу?",
    "Secure workspace": "Хамгаалагдсан ажлын талбар",
    "Open your broadcast dashboard, contacts, audio files, and campaign reports.":
        "Кампанит ажил, харилцагч, аудио, тайлангийн самбар руу нэвтэрнэ үү.",
    "Company workspace": "Байгууллагын ажлын талбар",
    "Company name": "Байгууллагын нэр",
    "Your name": "Таны нэр",
    "Confirm password": "Нууц үг давтах",
    "Create one company workspace and your first admin account.":
        "Байгууллагын ажлын талбар болон эхний админ хэрэглэгчээ үүсгэнэ.",
    "Minimum 6 characters. Maximum 72 bytes.":
        "Хамгийн багадаа 6 тэмдэгт, ихдээ 72 байт.",
    "Passwords do not match.": "Нууц үг таарахгүй байна.",
    "Reset your password": "Нууц үг сэргээх",
    "Reset password": "Нууц үг сэргээх",
    "Back to login": "Нэвтрэх хуудас руу буцах",
    "Send code": "Код илгээх",
    "Enter code": "Код оруулах",
    "Enter your code": "Кодоо оруулна уу",
    "Code": "Код",
    "6-digit code": "6 оронтой код",
    "New password": "Шинэ нууц үг",
    "Enter your account email. If it's registered, we'll email you a 6-digit code.":
        "Бүртгэлтэй и-мэйл хаягаа оруулна уу. Бүртгэлтэй бол 6 оронтой код илгээнэ.",
    "Enter the 6-digit code we emailed you, plus your new password.":
        "И-мэйлээр ирсэн 6 оронтой код болон шинэ нууц үгээ оруулна уу.",
    "Didn't get a code? Request a new one":
        "Код ирээгүй юу? Дахин илгээх",
    "Show password": "Нууц үг харах",
    "Hide password": "Нууц үг нуух",
})

# ---------------------------------------------------------------- dashboard

_add("dashboard", {
    "Broadcast workspace": "Ажлын талбар",
    "Create campaign": "Кампанит ажил үүсгэх",
    "View campaigns": "Кампанит ажил харах",
    "New campaign": "Шинэ кампанит ажил",
    "Import contacts": "Харилцагч импортлох",
    "Audio library": "Аудио сан",
    "Cleanup": "Цэвэрлэгээ",
    "Draft": "Ноорог",
    "Queued / Running": "Дараалалд / Явагдаж буй",
    "Active contacts": "Идэвхтэй харилцагч",
    "Build a target list and preview it before any real start.":
        "Жагсаалт үүсгээд, эхлүүлэхээсээ өмнө урьдчилан шалгана.",
    "Upload CSV/TXT numbers and reuse existing company contacts.":
        "CSV/TXT дугаар оруулах болон бүртгэлтэй харилцагчаа ашиглах.",
    "Find campaigns that were created but never executed.":
        "Үүсгэсэн ч эхлүүлээгүй кампанит ажлуудыг олох.",
    # Placeholders keep the number out of the translated text, so word order
    # can differ between languages without concatenating fragments.
    "Manage reusable broadcast audio files. Current files: {count}.":
        "Дахин ашиглах аудио сан. Одоогийн файл: {count}.",
})

# -------------------------------------------------------------------- errors

_add("errors", {
    "Go to dashboard": "Хянах самбар руу очих",
    "Please sign in": "Нэвтэрнэ үү",
    "Page not found": "Хуудас олдсонгүй",
    "You do not have access to this": "Танд энэ хандах эрх байхгүй",
    "Not enough call tokens": "Дуудлагын эрх хүрэлцэхгүй байна",
    "Something went wrong on our side": "Манай талд алдаа гарлаа",
    "Something was wrong with that request": "Хүсэлтэд алдаа байна",
    "That action conflicts with the current state":
        "Энэ үйлдэл одоогийн төлөвтэй зөрчилдөж байна",
})

# ------------------------------------------------------------------ campaigns

_add("campaigns", {
    "Campaign": "Кампанит ажил",
    "Campaigns": "Кампанит ажил",
    "All Campaigns": "Бүх кампанит ажил",
    "Campaign Name": "Кампанит ажлын нэр",
    "Campaign No.": "Кампанит ажлын дугаар",
    "Campaign Info": "Кампанит ажлын мэдээлэл",
    "Campaign detail": "Дэлгэрэнгүй",
    "Campaign builder": "Кампанит ажил үүсгэх",
    "Campaign monitor": "Хяналт",
    "Campaign targets": "Дуудах жагсаалт",
    "Campaign Results for This Group": "Энэ бүлгийн кампанит ажлын үр дүн",
    "Campaigns Used": "Ашигласан кампанит ажил",
    "Create Campaign": "Кампанит ажил үүсгэх",
    "Create draft campaign": "Ноорог кампанит ажил үүсгэх",
    "Create Campaign and Open Dry-run": "Үүсгээд урьдчилсан харагдацыг нээх",
    "Cancel Campaign": "Кампанит ажлыг цуцлах",
    "Real Start Campaign": "Жинхэнэ эхлүүлэх",
    "Real Start Disabled": "Жинхэнэ эхлүүлэх боломжгүй",
    "Simulate This Campaign": "Туршилтаар ажиллуулах",
    "Open Campaign Detail": "Дэлгэрэнгүй харах",
    "Draft campaigns": "Ноорог кампанит ажил",
    "Editable Campaigns": "Засах боломжтой кампанит ажил",
    "Not Executed": "Эхлүүлээгүй",
    "Not executed": "Эхлүүлээгүй",
    "Not Executed Campaigns": "Эхлүүлээгүй кампанит ажил",
    "No campaigns yet": "Кампанит ажил алга байна",
    "Create your first campaign to start broadcasting.":
        "Эхний кампанит ажлаа үүсгээд дуудлага эхлүүлээрэй.",
    "Open a campaign to start calls, monitor progress, or download reports.":
        "Кампанит ажлаа нээж дуудлага эхлүүлэх, явцыг хянах, тайлан татах боломжтой.",
    "View campaign history, progress, and call results.":
        "Кампанит ажлын түүх, явц, дуудлагын үр дүнг харах.",
    "Start Readiness": "Эхлүүлэх бэлэн байдал",
    "Target Count": "Дуудах тоо",
    "Target Number Status": "Дугаарын төлөв",
    "Targets": "Дуудах дугаар",
    "Estimated unique targets": "Давхардалгүй дугаарын тоо",
    "Unique selected numbers": "Сонгосон давхардалгүй дугаар",
    "Total Numbers": "Нийт дугаар",
    "No numbers in this campaign.": "Энэ кампанит ажилд дугаар алга.",
    "No campaign targets found.": "Дуудах дугаар олдсонгүй.",
    "No not-executed campaigns.": "Эхлүүлээгүй кампанит ажил алга.",
    "Remove Selected From Campaign": "Сонгосныг кампанит ажлаас хасах",
    "Contacts and audio files will not be deleted when deleting a not-executed campaign.":
        "Эхлүүлээгүй кампанит ажлыг устгахад харилцагч болон аудио устахгүй.",
    "These campaigns are still editable. You can manage or remove numbers before execution.":
        "Эдгээр кампанит ажлыг засах боломжтой. Эхлүүлэхээс өмнө дугаарыг нэмэх, хасах боломжтой.",
    "This campaign has not been executed yet. You can remove selected numbers from this campaign.":
        "Энэ кампанит ажил хараахан эхлээгүй байна. Сонгосон дугаарыг хасах боломжтой.",
    "This campaign is locked because it was executed or has call logs. Numbers cannot be removed.":
        "Энэ кампанит ажил эхэлсэн буюу дуудлагын бүртгэлтэй тул дугаар хасах боломжгүй.",
    "Showing first 300 targets only. Use Manage Numbers to view more.":
        "Зөвхөн эхний 300 дугаарыг харуулж байна. Бүгдийг харахын тулд «Дугаар удирдах» руу орно уу.",
    "This page creates only a draft campaign and frozen target list.\n    It will not call anyone. After create, dry-run opens automatically.":
        "Энэ хуудас зөвхөн ноорог кампанит ажил болон дуудах жагсаалт үүсгэнэ. Хэнд ч залгахгүй. Үүсгэсний дараа урьдчилсан харагдац нээгдэнэ.",
    "Confirm target count before running simulation or real campaign start.":
        "Туршилт эсвэл жинхэнэ эхлүүлэхээс өмнө дуудах тоогоо шалгана уу.",
    "Selected groups, pasted numbers, imported file contacts, and selected existing contacts will be frozen into this campaign.":
        "Сонгосон бүлэг, буулгасан дугаар, импортолсон файл, сонгосон харилцагчид энэ кампанит ажилд бүртгэгдэнэ.",
    "Dry-run preview": "Урьдчилсан харагдац",
    "Preview Summary": "Урьдчилсан дүн",
    "This preview shows the frozen target list. No real calls will be made from this page.":
        "Энэ хуудас дуудах жагсаалтыг харуулна. Эндээс жинхэнэ дуудлага хийгдэхгүй.",
    "No frozen target contacts found. Do not start or simulate this campaign.":
        "Дуудах харилцагч олдсонгүй. Энэ кампанит ажлыг эхлүүлэх шаардлагагүй.",
    "These contacts are already frozen into this campaign target list.":
        "Эдгээр харилцагч энэ кампанит ажлын жагсаалтад орсон байна.",
    "Contacts That Would Be Called": "Дуудагдах харилцагчид",
    "No contacts found in this dry-run preview.": "Урьдчилсан харагдацад харилцагч олдсонгүй.",
    "1. Select contact groups": "1. Харилцагчийн бүлэг сонгох",
    "2. Add one number": "2. Нэг дугаар нэмэх",
    "3. Paste many numbers": "3. Олон дугаар буулгах",
    "4. Import CSV/TXT contacts": "4. CSV/TXT файл импортлох",
    "5. Select existing contacts like gallery": "5. Бүртгэлтэй харилцагчаас сонгох",
})

# ------------------------------------------------------------------- contacts

_add("contacts", {
    "Contact": "Харилцагч",
    "Contacts": "Харилцагч",
    "Contact List": "Харилцагчийн жагсаалт",
    "Contact detail": "Харилцагчийн дэлгэрэнгүй",
    "Contact Workspace": "Харилцагчийн хэсэг",
    "Contact group": "Харилцагчийн бүлэг",
    "Contact groups": "Харилцагчийн бүлэг",
    "Contact Groups": "Харилцагчийн бүлэг",
    "New Contact Group": "Шинэ бүлэг",
    "Group": "Бүлэг",
    "Groups": "Бүлэг",
    "Groups Used": "Ашигласан бүлэг",
    "Group Name": "Бүлгийн нэр",
    "Group name": "Бүлгийн нэр",
    "Group No.": "Бүлгийн дугаар",
    "Group Information": "Бүлгийн мэдээлэл",
    "Create Group": "Бүлэг үүсгэх",
    "Delete Group": "Бүлэг устгах",
    "Import Group": "Бүлэг импортлох",
    "+ Import New Group": "+ Шинэ бүлэг импортлох",
    "+ Import new group": "+ Шинэ бүлэг импортлох",
    "Members": "Гишүүд",
    "Total Members": "Нийт гишүүн",
    "Selected groups": "Сонгосон бүлэг",
    "Selected contacts": "Сонгосон харилцагч",
    "No groups selected.": "Бүлэг сонгоогүй байна.",
    "No members in this group.": "Энэ бүлэгт гишүүн алга.",
    "No contact groups yet.": "Бүлэг алга байна.",
    "No contacts found.": "Харилцагч олдсонгүй.",
    "No active contacts found.": "Идэвхтэй харилцагч олдсонгүй.",
    "No numbers selected yet.": "Дугаар сонгоогүй байна.",
    "This contact is not in any active group.": "Энэ харилцагч ямар ч идэвхтэй бүлэгт алга.",
    "This group has not been used in any campaign yet.":
        "Энэ бүлэг ямар ч кампанит ажилд ашиглагдаагүй байна.",
    "Active and inactive contacts currently linked to this group.":
        "Энэ бүлэгт холбогдсон идэвхтэй болон идэвхгүй харилцагчид.",
    "Deleting a group will not delete contacts.": "Бүлэг устгахад харилцагч устахгүй.",
    "Manage reusable contact groups for campaign targeting.":
        "Кампанит ажилд ашиглах харилцагчийн бүлгүүдийг удирдах.",
    "Create a reusable target group from pasted numbers or a CSV/TXT file.":
        "Буулгасан дугаар эсвэл CSV/TXT файлаас дахин ашиглах бүлэг үүсгэх.",
    "Campaign performance calculated only for numbers inside this contact group.":
        "Зөвхөн энэ бүлгийн дугаарын үр дүнг тооцсон.",
    "Select one or more groups. All numbers inside selected groups will be added to this campaign.":
        "Нэг буюу хэд хэдэн бүлэг сонгоно уу. Сонгосон бүлгийн бүх дугаар нэмэгдэнэ.",
    "Select contacts to deactivate or restore. Call logs and reports will remain.":
        "Идэвхгүй болгох эсвэл сэргээх харилцагчаа сонгоно уу. Бүртгэл, тайлан хэвээр үлдэнэ.",
    "Click one card to select. Hold mouse and drag over cards to select many.":
        "Нэг картыг дарж сонгоно. Хулганаа даран чирж олныг сонгоно.",
    "Search Contacts": "Харилцагч хайх",
    "Search phone or name": "Утас эсвэл нэрээр хайх",
    "Phone number": "Утасны дугаар",
    "Paste numbers": "Дугаар буулгах",
    "One number per line, or separated by comma/space.":
        "Мөр бүрт нэг дугаар, эсвэл таслал/хоосон зайгаар тусгаарлана.",
    "One phone number per line, or separated by comma/space.":
        "Мөр бүрт нэг дугаар, эсвэл таслал/хоосон зайгаар тусгаарлана.",
    "Import Contacts": "Харилцагч импортлох",
    "Import Result": "Импортын үр дүн",
    "Upload File": "Файл оруулах",
    "Upload a CSV or TXT file. Existing company numbers are skipped.":
        "CSV эсвэл TXT файл оруулна. Бүртгэлтэй дугаарыг алгасна.",
    "Or upload CSV/TXT": "Эсвэл CSV/TXT оруулах",
    "CSV or TXT file": "CSV эсвэл TXT файл",
    "CSV format: {columns}": "CSV бүтэц: {columns}",
    "TXT: one phone number per line.": "TXT: мөр бүрт нэг дугаар.",
    "TXT: one phone per line. CSV:": "TXT: мөр бүрт нэг дугаар. CSV:",
    "Import CSV or TXT files, organize groups, and keep duplicate numbers clean.":
        "CSV, TXT файл оруулж, бүлэг зохион байгуулж, давхардлыг цэвэрлэнэ.",
    "Deactivate Selected": "Сонгосныг идэвхгүй болгох",
    "Restore Selected": "Сонгосныг сэргээх",
    "Select All": "Бүгдийг сонгох",
    "Clear selected": "Сонголтыг арилгах",
    "0 selected": "0 сонгосон",
    "Optional group description": "Бүлгийн тайлбар (заавал бус)",
    "Optional name": "Нэр (заавал бус)",
    "Notes": "Тэмдэглэл",
    "Total Rows": "Нийт мөр",
    "Current active contacts:": "Одоогийн идэвхтэй харилцагч:",
    "Active contacts:": "Идэвхтэй харилцагч:",
})

# ---------------------------------------------------------------------- audio

_add("audio", {
    "Audio": "Аудио",
    "Audio Library": "Аудио сан",
    "Audio File": "Аудио",
    "Audio Files": "Аудио",
    "Audio file": "Аудио файл",
    "Audio No.": "Дугаар",
    "Audio length": "Аудионы урт",
    "Upload Audio": "Аудио оруулах",
    "Upload New Audio": "Шинэ аудио оруулах",
    "Record Your Voice": "Аудиогоо бичих",
    "Record your own voice, upload a file, or generate speech from text.":
        "Өөрийн аудиогоо бичих, файл оруулах, эсвэл бичвэрээс дуу үүсгэх.",
    "Your browser will ask for microphone permission the first time. Maximum length: {limit}.":
        "Эхний удаад хөтөч микрофон ашиглах зөвшөөрөл асууна. Дээд урт: {limit}.",
    "This browser cannot record audio on this page. You can still upload a file below.":
        "Энэ хөтчөөр бичлэг хийх боломжгүй. Доор файл оруулах боломжтой.",
    "Supported formats: {formats}. Maximum duration: {limit}.":
        "Дэмжигдэх формат: {formats}. Дээд урт: {limit}.",
    "Supported formats: {formats}. Max {size}.":
        "Дэмжигдэх формат: {formats}. Дээд хэмжээ: {size}.",
    "Generate Audio": "Дуу үүсгэх",
    "Generate from Text (Text-to-Speech)": "Бичвэрээс дуу үүсгэх",
    "Type the message text below and choose a voice.":
        "Доор бичвэрээ оруулаад хоолойгоо сонгоно уу.",
    "Message text": "Мессежийн бичвэр",
    "Voice": "Хоолой",
    "Mongolian — Female (Yesui)": "Монгол — Эмэгтэй (Есүй)",
    "Mongolian — Male (Bataa)": "Монгол — Эрэгтэй (Батаа)",
    "Play any file, rename it, or cut a section out of it.":
        "Файлаа сонсох, нэрийг өөрчлөх, эсвэл хэсэглэн тайрах боломжтой.",
    "No audio files yet. Record your voice, upload a file, or generate one from text.":
        "Аудио алга байна. Бичих, файл оруулах, эсвэл бичвэрээс үүсгэнэ үү.",
    "Listen": "Сонсох",
    "Listen back before saving": "Хадгалахаасаа өмнө сонсоно уу",
    "Name this recording": "Бичлэгийн нэр",
    "Morning promo message": "Өглөөний урамшууллын мессеж",
    "Save to library": "Санд хадгалах",
    "Discard": "Устгах",
    "● Start recording": "● Бичиж эхлэх",
    "■ Stop": "■ Зогсоох",
    "Rename": "Нэр өөрчлөх",
    "Save name": "Нэрийг хадгалах",
    "Trim": "Тайрах",
    "▶ Preview selection": "▶ Сонгосныг сонсох",
    "Save trimmed copy": "Тайрсан хувилбарыг хадгалах",
    "Replace the original": "Эх файлыг солих",
    "Start —": "Эхлэл —",
    "End —": "Төгсгөл —",
    "Keeps": "Үлдэх урт",
    "Saved": "Хадгалагдлаа",
})

# ------------------------------------------------------------------------ stt

_add("stt", {
    "Speech to Text": "Яриа таних",
    "Voicebro STT": "Voicebro яриа таних",
    "Upload audio and get a Mongolian transcript. Processing takes roughly\n        1-3x the audio's length, so a 1 minute file can take a few minutes.":
        "Аудио оруулж монгол бичвэр авна. Боловсруулалт нь аудионы уртаас 1-3 дахин их хугацаа авдаг тул 1 минутын файл хэдэн минут болно.",
    "Transcribe": "Бичвэр болгох",
    "Processing time": "Боловсруулсан хугацаа",
    "Speed": "Хурд",
    "Segments": "Хэсгүүд",
    "Result": "Үр дүн",
    "Text": "Бичвэр",
    "Time": "Хугацаа",
})

# ---------------------------------------------------------------- call status

_add("status", {
    "Completed": "Дууссан",
    "Completed At": "Дууссан огноо",
    "Busy": "Завгүй",
    "No answer": "Хариулаагүй",
    "No Answer": "Хариулаагүй",
    "Failed": "Амжилтгүй",
    "Cancelled": "Цуцлагдсан",
    "cancelled": "цуцлагдсан",
    "Pending": "Хүлээгдэж буй",
    "Calling": "Дуудаж байна",
    "Answered": "Хариулсан",
    "Congestion": "Сүлжээ завгүй",
    "Skipped": "Алгасагдсан",
    "Started": "Эхэлсэн",
    "Ended": "Дууссан",
    "Progress": "Явц",
    "Attempts": "Оролдлого",
    "Cause": "Шалтгаан",
    "Call History": "Дуудлагын түүх",
    "Call Results": "Дуудлагын үр дүн",
    "Call Log ID": "Дуудлагын дугаар",
    "History": "Түүх",
    "Result": "Үр дүн",
    "Total": "Нийт",
    "All": "Бүгд",
    "All statuses": "Бүх төлөв",
    "Active": "Идэвхтэй",
    "active": "идэвхтэй",
    "Inactive": "Идэвхгүй",
    "inactive": "идэвхгүй",
    "Not Active": "Идэвхгүй",
    "Available": "Боломжтой",
    "Not Available": "Боломжгүй",
    "Applied": "Хэрэгжсэн",
    "Not Applied": "Хэрэгжээгүй",
    "Ready": "Бэлэн",
    "Status:": "Төлөв:",
    "Active:": "Идэвхтэй:",
    "No call history yet.": "Дуудлагын түүх алга.",
    "No calls recorded yet.": "Бүртгэгдсэн дуудлага алга.",
    "Previous call attempts and results for this contact.":
        "Энэ харилцагч руу хийсэн өмнөх дуудлага, үр дүн.",
    "seconds": "секунд",
    "calls in progress": "явагдаж буй дуудлага",
})

# -------------------------------------------------------- profile and company

_add("profile", {
    "Profile": "Профайл",
    "Company profile": "Байгууллагын профайл",
    "Your Account": "Таны бүртгэл",
    "Save Profile": "Профайл хадгалах",
    "Full name": "Бүтэн нэр",
    "Role": "Үүрэг",
    "Company": "Байгууллага",
    "Company Users": "Байгууллагын хэрэглэгчид",
    "Company email": "Байгууллагын и-мэйл",
    "Company phone": "Байгууллагын утас",
    "Save Company": "Байгууллага хадгалах",
    "Change Password": "Нууц үг солих",
    "Current password": "Одоогийн нууц үг",
    "Confirm new password": "Шинэ нууц үг давтах",
    "Manage your account, password, company information, and company users.":
        "Бүртгэл, нууц үг, байгууллагын мэдээлэл болон хэрэглэгчдээ удирдана.",
    "Users registered under this company account.":
        "Энэ байгууллагад бүртгэлтэй хэрэглэгчид.",
    "Your role can view company information, but cannot edit it.":
        "Таны эрх байгууллагын мэдээллийг харах боломжтой ч засах боломжгүй.",
    "No users found.": "Хэрэглэгч олдсонгүй.",
    "One token = one answered call. Busy, no-answer and failed calls cost nothing.":
        "Нэг эрх = нэг хариулсан дуудлага. Завгүй, хариулаагүй, амжилтгүй дуудлагад эрх зарцуулагдахгүй.",
    "No call tokens left. Buy a package below before starting a campaign.":
        "Дуудлагын эрх дууссан байна. Доорх багцаас аваарай.",
})

# ------------------------------------------------------------- admin/SIP page

_add("admin", {
    "Platform Admin": "Системийн админ",
    "Voicebro Admin": "Voicebro админ",
    "SIP Number": "SIP дугаар",
    "SIP Numbers": "SIP дугаар",
    "SIP Trunks / SIP Numbers": "SIP суваг / дугаар",
    "Current SIP Numbers": "Одоогийн SIP дугаарууд",
    "Add SIP Number": "SIP дугаар нэмэх",
    "Add & Apply to Asterisk": "Нэмээд хэрэгжүүлэх",
    "Enable & Apply": "Идэвхжүүлээд хэрэгжүүлэх",
    "Manage Numbers": "Дугаар удирдах",
    "SIP Username": "SIP хэрэглэгчийн нэр",
    "SIP username": "SIP хэрэглэгчийн нэр",
    "SIP Password": "SIP нууц үг",
    "SIP password": "SIP нууц үг",
    "SIP Host / Server IP": "SIP хост / сервер IP",
    "SIP Domain / Realm": "SIP домэйн",
    "SIP availability": "SIP боломж",
    "Provider": "Нийлүүлэгч",
    "Host": "Хост",
    "Domain": "Домэйн",
    "Username": "Хэрэглэгчийн нэр",
    "Endpoint": "Холболтын цэг",
    "Register Status": "Бүртгэлийн төлөв",
    "Registered": "Бүртгэгдсэн",
    "Unregistered": "Бүртгэгдээгүй",
    "Rejected": "Татгалзсан",
    "Active Calls": "Идэвхтэй дуудлага",
    "Max Concurrent Calls": "Зэрэг дуудлагын дээд тоо",
    "Free Slots": "Сул суваг",
    "3 free slots": "3 сул суваг",
    "Disable": "Идэвхгүй болгох",
    "Disabled": "Идэвхгүй",
    "DB Status": "Өгөгдлийн сангийн төлөв",
    "Description": "Тайлбар",
    "Max": "Дээд",
    "No SIP numbers configured yet.": "SIP дугаар тохируулаагүй байна.",
    "No SIP number is configured or visible for this campaign.":
        "Энэ кампанит ажилд SIP дугаар тохируулаагүй байна.",
    "Only registered SIP numbers with free slots are available for real start.":
        "Зөвхөн бүртгэгдсэн, сул сувагтай SIP дугаар жинхэнэ эхлүүлэхэд ашиглагдана.",
    "Registered means Asterisk currently has successful SIP registration.":
        "Бүртгэгдсэн гэдэг нь SIP бүртгэл амжилттай хийгдсэн гэсэн үг.",
    "Add, enable, disable, and check Asterisk registration status for outbound SIP trunks.":
        "Гарах SIP сувгийг нэмэх, идэвхжүүлэх, идэвхгүй болгох, бүртгэлийн төлөв шалгах.",
    "The endpoint is generated automatically from the provider and SIP number. Example: {example}":
        "Холболтын цэг нь нийлүүлэгч болон SIP дугаараас автоматаар үүснэ. Жишээ: {example}",
    "Example: cally or mobinet": "Жишээ: cally эсвэл mobinet",
    "Example: 77090909": "Жишээ: 77090909",
    "Example: 202.55.178.100 or portal.cally.mn": "Жишээ: 202.55.178.100 эсвэл portal.cally.mn",
    "Example: portal.cally.mn": "Жишээ: portal.cally.mn",
})

# ---------------------------------------------------------- generic UI chrome

_add("chrome", {
    "Refresh": "Шинэчлэх",
    "Manual": "Гараар",
    "Manual refresh mode.": "Гараар шинэчлэх горим.",
    "Auto-refresh off": "Автомат шинэчлэлт унтраалттай",
    "Monitor groups": "Хяналтын бүлэг",
    "Next": "Дараах",
    "Previous": "Өмнөх",
    "Open": "Нээх",
    "Clear": "Цэвэрлэх",
    "File": "Файл",
    "Download CSV": "CSV татах",
    "Reports": "Тайлан",
    "← Back to dashboard": "← Хянах самбар руу буцах",
    "← Back to campaign": "← Кампанит ажил руу буцах",
    "← Back to contacts": "← Харилцагч руу буцах",
    "← Back to groups": "← Бүлэг рүү буцах",
})

# ----------------------------------------------------------- public home page

_add("home", {
    "Enterprise voice broadcast workspace": "Байгууллагын дуут мэдээллийн систем",
    "One calm command center for campaigns, contacts, audio, and reports.":
        "Кампанит ажил, харилцагч, аудио, тайланг нэг дороос.",
    "Build targeted voice campaigns, preview unique numbers before launch,\n        monitor outcomes, and keep company data separated in one practical\n        communication platform.":
        "Зорилтот дуут кампанит ажил үүсгэж, эхлүүлэхийн өмнө дугаараа шалгаж, үр дүнг хянаж, байгууллагынхаа мэдээллийг тусад нь хадгална.",
    "Campaign Control": "Кампанит ажлын удирдлага",
    "Create campaigns from frozen contact lists and review targets before real start.":
        "Бэлдсэн жагсаалтаас кампанит ажил үүсгэж, эхлүүлэхийн өмнө шалгана.",
    "Upload reusable broadcast audio and track duration for reporting.":
        "Дахин ашиглах аудио оруулж, урт хугацааг тайланд бүртгэнэ.",
    "See call status, duration, and export campaign result CSV files.":
        "Дуудлагын төлөв, хугацааг харж, үр дүнг CSV болгон татна.",
    "Example report": "Жишээ тайлан",
    "Live-ready": "Ажиллахад бэлэн",
    "Frozen target list": "Бэлдсэн жагсаалт",
    "Duplicates removed": "Давхардал арилсан",
    "Report export": "Тайлан татах",
    "CSV ready": "CSV бэлэн",
    "Live status and duration tracking": "Төлөв, хугацааны хяналт",
})

_add("campaigns", {
    "No contact group was linked to this campaign.":
        "Энэ кампанит ажилд харилцагчийн бүлэг холбоогүй байна.",
})

# --------------------------------------------------- billing and QPay payment

_add("billing", {
    "Call Tokens": "Дуудлагын эрх",
    "Buy Tokens": "Багц авах",
    "Buy": "Авах",
    "Calls": "Дуудлага",
    "Amount": "Дүн",
    "Date": "Огноо",
    "Total owned": "Нийт эрх",
    "Held": "Хүлээгдэж буй",
    "Purchase History": "Худалдан авалтын түүх",
    "Number of calls": "Дуудлагын тоо",
    "{min}+ calls, {price}₮ per call": "{min}+ дуудлага, {price}₮ / дуудлага",
    "{count} calls ({price}₮ per call)": "{count} дуудлага ({price}₮ / дуудлага)",
    "QPay integration test": "QPay холболтын тест",
    "Creates a real {amount}₮ order to check the QPay connection. If it is paid, {count} call tokens are added.":
        "QPay холболтыг шалгахаар {amount}₮-ийн жинхэнэ захиалга үүсгэнэ. Төлөгдвөл {count} дуудлагын эрх нэмэгдэнэ.",
    "Create a {amount}₮ test payment": "{amount}₮ тест төлбөр үүсгэх",

    "Pay": "Төлбөр төлөх",
    "Order #{id}": "Захиалга #{id}",
    "{count} call tokens": "{count} дуудлагын эрх",
    "{count} call tokens have been added to your balance.":
        "{count} дуудлагын эрх таны дансанд нэмэгдлээ.",
    "Payment received": "Төлбөр амжилттай төлөгдлөө",
    "View balance": "Үлдэгдэл харах",
    "← Back to profile": "← Профайл руу буцах",
    "Pay by QR code": "QR кодоор төлөх",
    "Open your banking app and scan the QR code. This page updates automatically once the payment arrives.":
        "Банкны аппликейшнээ нээж QR кодыг уншуулна уу. Төлбөр төлөгдмөгц энэ хуудас автоматаар шинэчлэгдэнэ.",
    "QPay payment QR code": "QPay төлбөрийн QR код",
    "Waiting for payment...": "Төлбөр хүлээгдэж байна...",
    "Check payment": "Төлбөрөө шалгах",
    "Open QPay link": "QPay холбоос нээх",
    "Cancel this order?": "Энэ захиалгыг цуцлах уу?",
    "Pay with a banking app": "Банкны апп-аар төлөх",
    "On a phone, choosing your bank below opens its app directly.":
        "Утаснаасаа орсон бол доорх банкаа сонгоход апп шууд нээгдэнэ.",
    "No bank links were returned. Please use the QR code.":
        "Банкны холбоос ирсэнгүй. QR кодыг ашиглана уу.",

    "Payment received. Refreshing...": "Төлбөр амжилттай! Хуудсыг шинэчилж байна...",
    "This order is now": "Энэ захиалгын төлөв:",
    "The payment has not arrived yet. Please wait and check again.":
        "Төлбөр хараахан ирээгүй байна. Түр хүлээгээд дахин шалгана уу.",
    "Checking...": "Шалгаж байна...",
    "Could not check the payment:": "Шалгахад алдаа гарлаа:",
    "Waiting timed out. If you have paid, press Check payment.":
        "Хүлээх хугацаа дууслаа. Төлсөн бол «Төлбөрөө шалгах» дарна уу.",
})

# ----------------------------------------------------- server-side messages
#
# Raised as English deep in routes and services, where no request - and so no
# language - is available. The English text is what reaches logs and API
# clients; these entries are applied at render time by t() in the template.

_add("server.auth", {
    "Not logged in": "Нэвтрээгүй байна",
    "Invalid session": "Хүчингүй холболт",
    "Inactive user": "Идэвхгүй хэрэглэгч",
    "Incorrect email or password": "И-мэйл эсвэл нууц үг буруу",
    "Could not validate credentials": "Нэвтрэх мэдээллийг баталгаажуулж чадсангүй",
    "User not found or inactive": "Хэрэглэгч олдсонгүй эсвэл идэвхгүй",
    "Email already registered": "И-мэйл бүртгэлтэй байна",
    "Email is required": "И-мэйл оруулна уу",
    "Password is required": "Нууц үг оруулна уу",
    "Password is too long. Maximum is 72 bytes.": "Нууц үг хэт урт байна. Дээд хэмжээ 72 байт.",
    "Company or email already exists": "Байгууллага эсвэл и-мэйл бүртгэлтэй байна",
    "Company name is required": "Байгууллагын нэр оруулна уу",
    "Owner only": "Зөвхөн эзэмшигч",
    "Owner can only manage SIP numbers": "Эзэмшигч зөвхөн SIP дугаар удирдана",
    "Failed to create account: {reason}": "Бүртгэл үүсгэж чадсангүй: {reason}",
})

_add("server.campaigns", {
    "Campaign not found": "Кампанит ажил олдсонгүй",
    "Campaign name is required": "Кампанит ажлын нэр оруулна уу",
    "Campaign has no audio file.": "Кампанит ажилд аудио алга.",
    "Campaign has no frozen target_contact_ids": "Кампанит ажилд дуудах жагсаалт алга",
    "Campaign has no target contacts. Create a new campaign first.":
        "Кампанит ажилд дуудах харилцагч алга. Шинээр үүсгэнэ үү.",
    "No active contacts found for campaign": "Кампанит ажилд идэвхтэй харилцагч олдсонгүй",
    "No active frozen target contacts found for campaign":
        "Кампанит ажилд идэвхтэй дуудах харилцагч олдсонгүй",
    "No valid active contacts found for this campaign":
        "Энэ кампанит ажилд тохирох идэвхтэй харилцагч олдсонгүй",
    "Only draft campaigns can be started.": "Зөвхөн ноорог кампанит ажлыг эхлүүлнэ.",
    "Only running or queued campaigns can be cancelled.":
        "Зөвхөн явагдаж буй эсвэл дараалалд буй кампанит ажлыг цуцална.",
    "Only not-executed draft campaigns can be deleted.":
        "Зөвхөн эхлүүлээгүй ноорог кампанит ажлыг устгана.",
    "This campaign was already executed. Numbers cannot be removed.":
        "Энэ кампанит ажил аль хэдийн эхэлсэн тул дугаар хасах боломжгүй.",
    "Please select, add, paste, or import at least one phone number":
        "Дор хаяж нэг дугаар сонгох, нэмэх, буулгах эсвэл импортлоно уу",
    "Dry-run is disabled in this environment.": "Урьдчилсан горим идэвхгүй байна.",
    "Simulation is disabled in this environment.": "Туршилтын горим идэвхгүй байна.",
    "contact_ids must be positive integers": "contact_ids эерэг бүхэл тоо байх ёстой",
    "contact_limit must be greater than 0": "contact_limit 0-оос их байх ёстой",
})

_add("server.contacts", {
    "Contact not found": "Харилцагч олдсонгүй",
    "Contact group not found": "Харилцагчийн бүлэг олдсонгүй",
    "Group name is required": "Бүлгийн нэр оруулна уу",
    "Group name already exists": "Ийм нэртэй бүлэг байна",
    "No valid numbers found for this group": "Энэ бүлэгт тохирох дугаар олдсонгүй",
    "Duplicate contact found during import": "Импортлох үед давхардсан харилцагч илэрлээ",
    "Phone already exists for this company": "Энэ дугаар байгууллагад бүртгэлтэй байна",
    "Contact file must be UTF-8 encoded": "Файл UTF-8 кодлолтой байх ёстой",
    "File must be UTF-8 encoded": "Файл UTF-8 кодлолтой байх ёстой",
    "Only CSV or TXT contact files are allowed": "Зөвхөн CSV эсвэл TXT файл зөвшөөрнө",
    "Only CSV or TXT file allowed": "Зөвхөн CSV эсвэл TXT файл зөвшөөрнө",
    "Contacts not found or inactive: {missing_ids}":
        "Харилцагч олдсонгүй эсвэл идэвхгүй: {missing_ids}",
})

_add("server.audio", {
    "Audio file not found": "Аудио олдсонгүй",
    "Audio name is too long. Please keep it under 200 characters.":
        "Аудионы нэр хэт урт байна. 200 тэмдэгтээс богино байх ёстой.",
    "No audio file provided.": "Аудио файл оруулаагүй байна.",
    "Uploaded file is empty.": "Оруулсан файл хоосон байна.",
    "Only mp3, wav, m4a, ogg, flac files are allowed":
        "Зөвхөн mp3, wav, m4a, ogg, flac файл зөвшөөрнө",
    "The audio file is missing from the server's sounds folder.":
        "Аудио файл серверийн хавтаснаас олдсонгүй.",
    "Trimming failed: {reason}": "Тайрахад алдаа гарлаа: {reason}",
    "Could not reach the STT service: {reason}": "Яриа таних үйлчилгээнд холбогдож чадсангүй: {reason}",
})

_add("server.sip", {
    "SIP trunk not found": "SIP суваг олдсонгүй",
    "SIP trunk already exists": "SIP суваг бүртгэлтэй байна",
    "SIP number is required": "SIP дугаар оруулна уу",
    "SIP username is required": "SIP хэрэглэгчийн нэр оруулна уу",
    "SIP password is required": "SIP нууц үг оруулна уу",
    "SIP host is required": "SIP хост оруулна уу",
    "Selected SIP number is not available.": "Сонгосон SIP дугаар боломжгүй байна.",
    "No registered available SIP number.": "Бүртгэгдсэн, боломжтой SIP дугаар алга.",
    "No available SIP number. Check SIP registration and active calls.":
        "Боломжтой SIP дугаар алга. Бүртгэл болон идэвхтэй дуудлагаа шалгана уу.",
    "No active SIP trunk found for simulation CallLog trunk_id":
        "Туршилтад идэвхтэй SIP суваг олдсонгүй",
    "This SIP number is already active in CRM. Remove it first before adding again.":
        "Энэ SIP дугаар системд идэвхтэй байна. Дахин нэмэхийн өмнө устгана уу.",
})

_add("server.billing", {
    "Company not found.": "Байгууллага олдсонгүй.",
    "Package not found.": "Багц олдсонгүй.",
    "Purchase not found": "Захиалга олдсонгүй",
    "Purchase not found.": "Захиалга олдсонгүй.",
    "Cannot remove more tokens than the company has available.":
        "Байгууллагад байгаагаас илүү эрх хасах боломжгүй.",
    "This package needs at least {minimum} calls.":
        "Энэ багц хамгийн багадаа {minimum} дуудлага шаардана.",
    "Purchase is {status} and cannot be paid.":
        "Захиалгын төлөв {status} тул төлөх боломжгүй.",
    "This order has no payment QR.": "Энэ захиалгад төлбөрийн QR алга.",
    "Test purchases are not enabled.": "Тест захиалга идэвхгүй байна.",
    "Only an admin can run a test purchase.": "Зөвхөн админ тест захиалга үүсгэнэ.",
    "Test purchase amount and call count must both be positive.":
        "Тест захиалгын дүн болон дуудлагын тоо эерэг байх ёстой.",
})

_add("server.flash", {
    "Invalid email or password": "И-мэйл эсвэл нууц үг буруу",
    "User is inactive": "Хэрэглэгч идэвхгүй байна",
    "Passwords do not match": "Нууц үг таарахгүй байна",
    "Password must be at least 6 characters": "Нууц үг дор хаяж 6 тэмдэгт байх ёстой",
    "Password is too long. Please use 72 bytes or less.":
        "Нууц үг хэт урт байна. 72 байтаас бага байх ёстой.",
    "Campaign queued": "Кампанит ажил дараалалд орлоо",
    "Campaign cancelled": "Кампанит ажил цуцлагдлаа",

    "Profile updated.": "Профайл шинэчлэгдлээ.",
    "Password changed.": "Нууц үг солигдлоо.",
    "Company information updated.": "Байгууллагын мэдээлэл шинэчлэгдлээ.",
    "Company record was not found.": "Байгууллагын бүртгэл олдсонгүй.",
    "Company name is required.": "Байгууллагын нэр оруулна уу.",
    "Company email is required.": "Байгууллагын и-мэйл оруулна уу.",
    "Email is required.": "И-мэйл оруулна уу.",
    "Current password is incorrect.": "Одоогийн нууц үг буруу байна.",
    "New password and confirmation do not match.": "Шинэ нууц үг давталттай таарахгүй байна.",
    "New password must be at least 6 characters.": "Шинэ нууц үг дор хаяж 6 тэмдэгт байх ёстой.",
    "Only company admins can update company information.":
        "Зөвхөн байгууллагын админ мэдээллийг засна.",
    "That email is already used by another account.":
        "Энэ и-мэйлийг өөр хэрэглэгч ашиглаж байна.",
    "That company email is already used by another company.":
        "Энэ и-мэйлийг өөр байгууллага ашиглаж байна.",
})
