DEFAULT_EXPENSE_CATEGORY = "未分类"

SUPPORTED_CATEGORIES = (
    "餐饮",
    "交通",
    "购物",
    "住房",
    "订阅",
    "娱乐",
    "医疗",
    "教育",
    "办公",
    "旅行",
    "个人护理",
    "生活服务",
    "家庭",
    "服饰",
    "数码",
    "健身",
    "礼物",
    "税费",
    "保险",
    "其他",
    DEFAULT_EXPENSE_CATEGORY,
)

SUPPORTED_CATEGORY_SET = frozenset(SUPPORTED_CATEGORIES)

CATEGORY_GUIDANCE = (
    (
        "餐饮",
        "meals, drinks, groceries, snacks, restaurants, hawker food, 白鸡饭, 福建面",
    ),
    ("交通", "taxi, ride-hailing, public transport, fuel, parking"),
    ("购物", "general retail purchases that do not fit a narrower category"),
    ("住房", "rent, mortgage, utilities, home maintenance"),
    ("订阅", "recurring software, media, membership, or service subscriptions"),
    ("娱乐", "movies, games, events, hobbies, leisure"),
    ("医疗", "doctor, pharmacy, dental, health treatment"),
    ("教育", "courses, books, training, school fees"),
    ("办公", "work supplies, business tools, office expenses"),
    ("旅行", "flights, hotels, trips, travel activities"),
    ("个人护理", "haircut/剪头发, grooming, beauty, skincare, massage, personal care"),
    ("生活服务", "laundry, repair, cleaning, postal, local services"),
    ("家庭", "childcare, family supplies, household shared costs"),
    ("服饰", "clothing, shoes, accessories"),
    ("数码", "electronics, gadgets, apps, device accessories"),
    ("健身", "gym, sports, fitness classes, exercise gear"),
    ("礼物", "gifts, red packets, donations for personal occasions"),
    ("税费", "tax, fines, government fees, administrative charges"),
    ("保险", "insurance premiums and insurance-related fees"),
    ("其他", "valid expense that is known but does not fit another category"),
    (DEFAULT_EXPENSE_CATEGORY, "use only when the category cannot be inferred"),
)
