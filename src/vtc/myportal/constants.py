from __future__ import annotations

MYPORTAL_URL = "https://myportal.vtc.edu.hk/wps/portal"
MYPORTAL_HOME = "https://myportal.vtc.edu.hk/wps/myportal/sp/"
SECTION_PATHS: dict[str, tuple[str, ...]] = {
    "timetable": (
        "https://myportal.vtc.edu.hk/wps/myportal/sp/timetable/",
    ),
    "activities": (),
    "modules": (),
    "documents": (),
}

SECTION_URL_MARKERS: dict[str, tuple[str, ...]] = {
    "timetable": ("/sp/timetable/",),
    "activities": ("/sp/actstud/",),
    "modules": ("/sp/omsstud/",),
    "documents": (
        "/pa_stportalwebservice/tokengen",
        "swsdownload.vtc.edu.hk/",
    ),
}

SECTION_LABELS: dict[str, tuple[str, ...]] = {
    "timetable": (
        r"Timetabling",
        r"Timetable",
        r"時間表",
        r"时间表",
        r"課表",
        r"课表",
    ),
    "activities": (
        r"Activity Enrolment",
        r"Activity Enrollment",
        r"Student Activity",
        r"活動報名",
        r"活动报名",
        r"學生活動",
        r"学生活动",
    ),
    "modules": (
        r"Online Module Selection",
        r"Module Selection",
        r"網上選科",
        r"网上选科",
        r"選科",
        r"选科",
    ),
    "documents": (
        r"Document Download",
        r"文件下載",
        r"文件下载",
    ),
}

PARENT_LABELS: dict[str, tuple[str, ...]] = {
    "timetable": (r"My Academics", r"學業", r"学业", r"我的學業"),
    "activities": (r"Campus Life", r"校園生活", r"校园生活"),
    "modules": (r"My Academics", r"學業", r"学业", r"我的學業"),
    "documents": (r"My Academics", r"學業", r"学业", r"我的學業"),
}

TRANSCRIPT_MARKERS = (
    "transcript",
    "academic record",
    "academic certificate",
    "成績",
    "成绩",
    "學業成績",
    "学业成绩",
    "成就證明",
    "成就证明",
)
TUITION_MARKERS = (
    "tuition",
    "fee notice",
    "fee statement",
    "debit note",
    "payment notice",
    "學費",
    "学费",
    "繳費",
    "缴费",
)

APPLY_BUTTON = r"Enrol|Enroll|Register|Apply|Sign up|報名|报名|申請|申请"
SELECT_BUTTON = r"Select|Add|Enrol|Enroll|Submit|選修|选择|選擇|提交"
SEARCH_BUTTON = r"Search|搜尋|搜寻"
LOGIN_BUTTON = r"Login|登入"
LOGOUT_MARKERS = ("log out", "logout", "登出")
