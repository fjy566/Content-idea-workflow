from __future__ import annotations

import re
from collections.abc import Iterable


CATEGORY_OPTIONS = ("IT科技", "财经商业", "社会民生", "政策时政", "国际", "健康", "文体娱乐", "其他")

_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "IT科技",
        (
            "ai", "人工智能", "大模型", "模型", "芯片", "半导体", "科技", "互联网", "软件", "开源",
            "机器人", "手机", "电脑", "数码", "操作系统", "数据库", "云计算", "编程", "开发者", "github",
            "openai", "deepseek", "claude", "gemini", "英伟达", "nvidia", "苹果", "iphone", "鸿蒙", "算力",
        ),
    ),
    (
        "健康",
        ("医疗", "医院", "医生", "疾病", "健康", "药品", "患者", "癌症", "疫苗", "养生", "保健", "渐冻症"),
    ),
    (
        "财经商业",
        (
            "经济", "财经", "金融", "股市", "股票", "基金", "银行", "利率", "房价", "楼市", "消费",
            "企业", "公司", "财报", "融资", "投资", "商业", "价格", "关税", "人民币", "美元", "就业",
        ),
    ),
    (
        "政策时政",
        (
            "国务院", "中央", "政府", "政策", "法规", "法律", "监管", "部委", "全国人大", "政协",
            "外交部", "公安部", "教育部", "工信部", "最高法", "最高检", "总书记", "主席", "总理",
        ),
    ),
    (
        "社会民生",
        (
            "社会", "民生", "教育", "学校", "学生", "职场", "老人", "儿童", "住房", "交通", "铁路",
            "天气", "台风", "暴雨", "洪水", "地震", "火灾", "诈骗", "直播", "婚姻", "养老", "旅游",
        ),
    ),
    (
        "文体娱乐",
        ("电影", "电视剧", "综艺", "明星", "音乐", "演唱会", "游戏", "体育", "足球", "篮球", "奥运", "文化", "博物馆"),
    ),
    (
        "国际",
        (
            "美国", "英国", "法国", "德国", "日本", "韩国", "俄罗斯", "乌克兰", "欧洲", "欧盟", "中东",
            "联合国", "以色列", "伊朗", "印度", "澳大利亚", "加拿大", "非洲", "international", "global",
        ),
    ),
)


def _contains_keyword(text: str, keyword: str) -> bool:
    if keyword.isascii():
        return re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", text) is not None
    return keyword in text


def categorize_topic(title: str, summary: str = "", tags: Iterable[str] | None = None) -> str:
    text = " ".join([title or "", summary or "", *(tags or [])]).lower()
    for category, keywords in _RULES:
        if any(_contains_keyword(text, keyword) for keyword in keywords):
            return category
    ascii_letters = len(re.findall(r"[a-z]", text))
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    if ascii_letters > 30 and ascii_letters > chinese_chars * 1.5:
        return "国际"
    return "其他"
