from app.categories import CATEGORY_OPTIONS, categorize_topic


def test_technology_has_priority_for_ai_business_news():
    assert categorize_topic("AI 芯片公司完成新一轮融资") == "IT科技"


def test_common_chinese_categories():
    assert categorize_topic("央行宣布调整利率") == "财经商业"
    assert categorize_topic("台风带来大范围强降雨") == "社会民生"
    assert categorize_topic("国务院发布新政策") == "政策时政"
    assert categorize_topic("医院公布疾病诊疗进展") == "健康"


def test_english_story_defaults_to_international():
    assert categorize_topic("European leaders discuss regional security and cross-border policy") == "国际"


def test_ai_does_not_match_inside_english_words():
    assert categorize_topic("Prime minister said the campaign will continue against inflation") != "IT科技"


def test_all_categories_are_stable():
    assert len(CATEGORY_OPTIONS) == len(set(CATEGORY_OPTIONS))
