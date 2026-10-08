# -*- coding: utf-8 -*-
"""
course.selectors —— 页面元素选择器集中管理

设计原则（重要）：
1. 学习通前端改版频繁，所以每个位置都给**多个候选选择器**，按顺序尝试，命中即止；
2. 结构性信息（如“课程链接”“章节目录链接”）优先用 **URL 特征 + 文本特征** 识别，
   而不是依赖某一次改版后的 class 名，鲁棒性远高于硬编码选择器；
3. 如需临时适配新版页面，在 user_config.py 里写：

       SELECTOR_OVERRIDES = {
           "catalog_section": ["#newCatalog a", ".chapter-list a"],
       }

   本模块会在导入时自动合并（覆盖式），无需改动业务代码。
4. 可用 `python main.py dump --url <页面地址>` 导出 HTML 到 data/dumps/ 便于定位选择器。
"""

from __future__ import annotations

from typing import Any, Dict, List

# ---------------------------------------------------------------- 登录态
SELECTORS: Dict[str, List[str]] = {
    # 登录成功的标志（任一命中即认为已登录）。仅作判断，不用于自动登录。
    "login_indicator": [
        "#userName", ".user-name", ".info_name", "#infoName",
        "a[href*='/logout']", ".logout", "[onclick*='logout']",
    ],
    # 未登录/登录框标志
    "login_form": ["#phone", "input[name='phone']", "#uname", "form[action*='login']"],

    # ---------------------------------------------------------------- 课程列表
    "course_card": [
        "li.dashCard", "div.course", ".course-list li", "#courseList li",
        "li.vcard", ".gongji .item", ".courseBody li",
    ],
    "course_card_title": ["p.course-name", ".courseName", "h3", ".tit", "a[title]"],
    "course_card_teacher": [".teacher", "p.course-teacher", ".info__teacher"],
    "course_card_progress": [".progress", ".process", "[class*='schedule']"],

    # ---------------------------------------------------------------- 章节目录
    "catalog_group_title": [
        ".chapter h3 a", ".catalog_name", "h2.cur", ".item tit", ".anchorDiv",
        "._unit .title", ".chapterName",
    ],
    "catalog_section": [
        "#catalog_status .chapter a", "#catalog_div a", "ul#cur li a",
        ".catalog_item a", ".learnChapter a", "a.clicktitle", ".articelDetails a",
        "#dir a", ".catalog a",
    ],
    # 章节页内“任务类型”标签（部分课程在标题后显示 [视频]/[作业]）
    "catalog_task_flag": [".flag", ".taskType", "span.label"],

    # ---------------------------------------------------------------- 成绩/学习分析
    "grade_tab": ["a:has-text('成绩')", "a:has-text('学习分析')", ".m_lesson a:has-text('成绩')",
                  "a[href*='grade']", "li:has-text('成绩') a"],
    "grade_rows": ["table tr", ".grade-list li", ".scorelist li", "ul.list li", ".tiTabel_tr",
                   "div[class*='grade'] li", ".el-table__row"],
    "grade_row_name": ["td:first-child", ".name", ".tit", "a", "span"],
    "grade_row_score": ["td", ".score", "[class*='score']", "[class*='fenshu']"],
    "grade_overview": ["[class*='totalScore']", ".score_total", "p:has-text('总评')",
                       ":text('你的成绩')", ".zongping", "span:has-text('成绩')"],

    # ---------------------------------------------------------------- 讨论区(计分讨论)
    # 话题列表(真机确认:groupweb topicList iframe)
    "disc_topic_link": ["a.topicli_link", ".topicli_link", "li a[href*='topic']", ".topicli_title"],
    "disc_topic_open": [".topic_interactive .comment", ".comment[onclick]", "li .comment"],
    "disc_topic_title": [".topicli_title_text", ".topicli_title", "a.topicli_link"],
    "disc_new_post_btn": ["a.createTopic", ":text('新建话题')", ":text('发表话题')", ".createTopic"],
    # 话题详情/发帖/回复(候选,待 discussion_topic 导出后校准)
    "disc_post_title": ["input[name='title']", ".edit_title input[name='title']", "#topicName",
                        "input[placeholder*='标题']", ".titleInput input", "input[type='text']"],
    "disc_post_body": ["#content", "textarea[name='content']", ".uediter", ".edui-body-container",
                       "div[contenteditable='true']", "textarea"],
    "disc_post_submit": [".edit_btn .jb_btn_92:not([class*='_disable'])", "a.jb_btn.sure", ".jb_btn.sure", "a:has-text('发表')", "button:has-text('发表')",
                         ".submitBtn", "a:has-text('发布')", "input[type='submit']"],
    "disc_reply_box": ["div[contenteditable='true']", "textarea[name='replyContent']", "#replyContent",
                       ".edui-body-container", "textarea"],
    "disc_reply_submit": [".addReply.jb_btn:not([class*='_disable'])", ".jb_btn:has-text('回复')", "a:has-text('发表回复')", "a:has-text('回复')",
                          "button:has-text('回复')", ".replyBtn", "input[type='submit']"],

    # ---------------------------------------------------------------- 小节内容
    "content_iframe": ["iframe#iframe", "iframe[src*='ananas']", "iframe[name='iframe']", "iframe"],
    "content_root": ["#content", "#mc_list", ".ans-modules", "body"],
    "module": [".ans-module", "[class*='module']", ".nodeItem", ".catalog-item", ".learnTask"],
    "module_title": ["h1", "h2", ".title", ".nodeItemtit"],
    "video_module": ["[class*='module_video']", ".ans-att-chaptersvideo", "div.videobox", ".videoBox"],
    "audio_module": ["[class*='module_audio']", ".vjs-audio"],
    "doc_module": ["[class*='module_doc']", ".ans-doc", ".document", ".fileList"],
    "ppt_module": ["[class*='ppt']", ".ppt", ".slide", ".encapsulated"],
    "work_entry": ["a[href*='/work/']", "a[href*='toWorkList']", "a[href*='/mooc2/work/']"],
    "exam_entry": ["a[href*='/exam/']", "a[href*='examTest']", "a[href*='try-lesson']", "a[href*='testPaper']"],
    # 小节页内“视频 / 章节测验”这类切换标签的可见文字（导航到章节检测用；可在 user_config 覆盖）
    "section_task_tab": ["章节检测", "章节测验", "检测", "测验"],

    # ---------------------------------------------------------------- 视频播放器
    "video_tag": ["video", "#media", ".vjs-tech", "video-js video"],
    "video_play_btn": [".vjs-play-control", ".prism-play-btn", "[class*='play-btn']"],
    "video_finished_flag": [".finished", "[class*='hasLearned']", ".has-study", ".learned"],

    # ---------------------------------------------------------------- 题目结构
    "question_root": [
        "div.TiMu", ".questionLi", ".question-item", ".topic", ".TiMu",
        "div[data-type='question']", ".fk_subject", ".quest",
        ".ans-videoquiz", ".tkTopic",                       # 视频随堂弹题（ananas video quiz）
    ],
    "question_stem": [
        ".ZtZk .qtContent", ".ZtZk", ".qtContent", ".question_name", ".stem",
        ".topicButton", "h4", ".questTitle", ".question-title",
        ".tkItem_title", ".tkTopic_title",                  # 视频随堂弹题题干
    ],
    "question_option": [
        ".TiMu label.TiT", "label[for^='cb']", ".answer li", ".choice", ".option",
        ".topic_option", "ul.options li",
        "li.ans-videoquiz-opt", ".tkItem_ul li",            # 视频随堂弹题选项
    ],
    "question_input": [
        "input[type='radio']", "input[type='checkbox']", "textarea", "input.blank",
        "input[name='ans-videoquiz-opt']",                  # 视频随堂弹题选项（无 id，用 name）
    ],
    "question_no": ["div.fl", ".question-number", ".num", "label.tiNumber"],
    "question_score": [".score", "span[fangfen]", ".topicScore"],
    # 平台批改结果（只读识别，用于自动整理错题）
    "result_correct_flag": [".True", ".right", ".daan .right", "[class*='correct']"],
    "result_wrong_flag": [".Wrong", ".error", "[class*='wrong']", ".AnswerState"],
    "result_answer_text": [".mark .StandardAnswer", ".daan", ".rightAnswer", ".AnswerContent"],
    "result_user_answer_text": [".myAnswer", ".yourAnswer", ".UserAnswer"],

    # ---------------------------------------------------------------- 考试页
    "exam_title": ["h2", ".exam-title", ".topTitle", ".test-title"],
    "exam_time_left": [".time", ".countTime", "#leftTime", "[class*='timer']", "[class*='surplus']"],
    "exam_submit_btn": ["#videoquiz-submit", "a.btnSubmit", "#submitBtn", "button:has-text('提交')",
                        "a:has-text('提交答案')", "a:has-text('提交')",
                        "a.ans-videoquiz-submit:has-text('提交')", "button:has-text('交卷')", "a:has-text('交卷')"],
    "exam_save_btn": ["#tempsave", "a:has-text('暂时保存')", "button:has-text('暂时保存')",
                      "span:has-text('暂时保存')", "a:has-text('自动保存')"],   # 只存答案不交卷
    "exam_confirm_btn": ["#popok", ".layui-layer-btn0", "a:has-text('确定')", "button:has-text('确定')",
                         "a:has-text('确认')"],                                  # 交卷后的“确定”弹窗按钮
    "video_quiz_continue": ["#videoquiz-continue", "a.ans-videoquiz-continue:has-text('继续')"],  # 仅视频弹题自己的“继续”
    "exam_rule_text": ["p", ".tips", ".prompt", ".notice", "div[style]"],

    # ---------------------------------------------------------------- 通用弹层
    "popup_close": [
        ".layui-layer-close", "#closeBtn", ".close-pop", "a.gobtn",
        "[aria-label='Close']", ".dialog .close",
    ],
}

# --------------------------------------------------------------- 类型识别关键词
KIND_KEYWORDS: Dict[str, List[str]] = {
    "video": ["视频", "影片", "lesson", "重播", "时长"],
    "document": ["文档", "资料", "pdf", "讲义", "阅读"],
    "ppt": ["ppt", "课件", "幻灯片", "slide"],
    "exam": ["考试", "测验", "期末", "期中", "随堂测"],
    "work": ["作业", "任务点", "练习"],
    "practice": ["练习", "自测", "题库", "章节练习"],
    "discussion": ["讨论", "发帖"],
    "live": ["直播", "回放"],
}

# --------------------------------------------------------------- URL 特征（最稳定的识别方式）
URL_PATTERNS: Dict[str, List[str]] = {
    "course_home": ["mycourse/stu", "/course/studycourse", "/mooc2-ans/course",
                    "/courselist/entercoursenewfy", "/fyportal/courselist", "/fyportal/course"],
    "catalog": ["knowledge/atlas", "/course/", "studycourse", "knowledge"],
    "section": ["/knowledge/", "/work/", "/doc/", "/ppt/", "/exam/", "ananas", "mooc2-ans"],
    "video": ["ananas/status", "playVideo", "/video/", "video-ans"],
    "document": ["/doc/edit", "/doc/infourl", "/doc/", ".pdf", "/download"],
    "ppt": ["/ppt/", "/pptedit", "slide"],
    "exam": ["/exam/", "examTest", "try-lesson", "testPaper", "/mooc2/exam"],
    "work": ["/work/", "toWorkList", "work/list", "/mooc2/work"],
}

# 任务类型中文名，界面展示统一使用
KIND_LABELS: Dict[str, str] = {
    "video": "视频",
    "document": "文档",
    "ppt": "PPT课件",
    "exam": "章节测验",
    "work": "作业",
    "practice": "练习题",
    "discussion": "讨论",
    "live": "直播回放",
    "other": "其他",
}



def selectors(key: str) -> List[str]:
    """取候选选择器列表；不存在则返回空列表（调用方需容忍）。"""
    return list(SELECTORS.get(key, []))


def apply_overrides(overrides: Dict[str, Any] | None) -> None:
    """允许 user_config 覆盖/追加选择器。"""
    if not overrides:
        return
    for key, value in overrides.items():
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            continue
        cleaned = [str(v).strip() for v in value if str(v).strip()]
        if not cleaned:
            continue
        if key in SELECTORS:
            # 用户提供的优先级更高，但保留内置候选作为兜底
            SELECTORS[key] = cleaned + [s for s in SELECTORS[key] if s not in cleaned]
        else:
            SELECTORS[key] = cleaned
    _ = selectors  # 提示：外部主要通过 selectors() 访问
__all__ = ['SELECTORS', 'selectors', 'apply_overrides', 'KIND_KEYWORDS', 'KIND_LABELS', 'URL_PATTERNS']
