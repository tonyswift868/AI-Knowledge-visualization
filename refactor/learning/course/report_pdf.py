"""Local vector PDF rendering for the saved learning report (Qt only)."""
from __future__ import annotations

from pathlib import Path


def render_report_pdf(report, titles, views, path: Path):
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPdfWriter, QPageSize

    writer = QPdfWriter(str(path))
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setResolution(96)
    writer.setTitle('学习报告：' + report['plan']['goal'])
    painter = QPainter(writer)
    if not painter.isActive():
        raise OSError('无法创建 PDF。')
    families = set(QFontDatabase.families())
    family = next((f for f in ('Microsoft YaHei', 'Noto Sans CJK SC', 'SimSun', 'Arial Unicode MS')
                   if f in families), QFont().family())
    width, height = writer.width(), writer.height()
    margin, y = 46, 46
    usable = width - 2 * margin
    ink, muted, blue, rose = QColor('#182536'), QColor('#526275'), QColor('#2466a4'), QColor('#ae475b')

    def page(need=30):
        nonlocal y
        if y + need > height - margin:
            writer.newPage()
            y = margin

    def line(value, *, size=10, bold=False, color=ink, indent=0):
        nonlocal y
        font = QFont(family, size)
        font.setBold(bold)
        painter.setFont(font)
        painter.setPen(color)
        rect = QRectF(margin + indent, y, usable - indent, 2000)
        bounds = painter.boundingRect(rect, Qt.TextFlag.TextWordWrap, str(value))
        page(bounds.height() + 7)
        rect.moveTop(y)
        painter.drawText(rect, Qt.TextFlag.TextWordWrap, str(value))
        y += bounds.height() + 7

    def heading(value):
        nonlocal y
        page(46)
        y += 13
        line(value, size=14, bold=True, color=blue)

    def bar(label, value, total, color=blue, *, count=False):
        nonlocal y
        page(35)
        label_width = usable * .42
        painter.setPen(ink)
        painter.setFont(QFont(family, 9))
        painter.drawText(QRectF(margin, y, label_width - 4, 22),
                         Qt.TextFlag.TextSingleLine, label[:34])
        x = margin + label_width
        bar_width = usable - label_width - 95
        painter.fillRect(QRectF(x, y + 4, bar_width, 11), QColor('#e6ecf2'))
        painter.fillRect(QRectF(x, y + 4, bar_width * min(1, value / max(1, total)), 11), color)
        painter.drawText(QRectF(x + bar_width + 8, y, 86, 22),
                         Qt.TextFlag.TextSingleLine, f'{value} 次' if count else f'{value}/{total}')
        y += 27

    def daily(row):
        nonlocal y
        page(42)
        label = f"{row['day']}  目标 {row['target_minutes']}  实际 {row['actual_minutes']}"
        line(label + f"  比当天目标少 {row['debt_minutes']}  多 {row['ahead_minutes']}", size=9)
        x, span = margin + 110, usable - 250
        limit = max(1, row['target_minutes'], row['actual_minutes'])
        painter.fillRect(QRectF(x, y - 4, span * row['target_minutes'] / limit, 3), blue)
        painter.fillRect(QRectF(x, y + 1, span * row['actual_minutes'] / limit, 3), rose)
        y += 10

    try:
        plan = report['plan']
        line('学习报告：' + plan['goal'], size=19, bold=True, color=blue)
        line('最终目标：' + plan['final_goal'])
        line(f"计划日期：{plan['start_date']} → {plan['end_date']}；实际开始：{plan.get('actual_start_date') or '—'}；完成：{plan.get('actual_completion_date') or '—'}")
        line(f"学习时长：{plan['credited_minutes']} 分钟（计划 {plan['planned_minutes']} 分钟）；调整过计划 {plan['replan_count']} 次")
        checks = sum(p['check_questions'] for p in report.get('questions',[]))
        correct = sum(p['check_correct'] for p in report.get('questions',[]))
        has_questions = bool(report.get('questions'))
        line(f'最终检验：{correct}/{checks} 题答对' if checks else
             '最终检验：尚未进行。' if has_questions else '资料没有题，本计划只学习，不考核。')
        if plan.get('early_days'):
            line(f"比截止日提前 {plan['early_days']} 天完成。")
        if plan.get('overdue_days'):
            line(f"比截止日晚了 {plan['overdue_days']} 天完成。")
        if has_questions:
            heading('各模块掌握情况')
            for module in views['modules']:
                if module['total']:
                    bar(module['title'], module['mastered'], module['total'])
                else:
                    line(module['title'] + '：只学不考')
            heading('错误分布')
            for label, key in (('随口问', 'casual'), ('模块检测', 'module'), ('最终检验', 'check')):
                count = views['errors'][key]
                bar(label, count, max(1, views['errors']['casual'], views['errors']['module'],
                                      views['errors']['check']), rose, count=True)
        heading('每日目标与实际时长 / 历史明细')
        line('蓝色为计划，红色为实际；每天单独比较当天目标与实际，不是累计欠账。', size=9, color=muted)
        for row in views['history']:
            daily(row)
        heading('最需要注意的知识点')
        weak = [p for p in report['weakest_points'] if p['score']]
        for point in weak:
            line(f"{titles.get(point['point_id'], point['point_id'])}：出错 {point['score']} 次")
        if not weak:
            line('没有明显的薄弱点。')
        if report.get('live_points'):
            heading('老师现场讲的知识点')
            line('以下知识点由老师结合资料现场讲解：', size=9, color=muted)
            for point in report['live_points']:
                line(point['title'])
        heading('各知识点老师评语')
        comments = report.get('point_comments') or {}
        for point in report['points']:
            pid = point['point_id']
            line(f"{titles.get(pid, point['summary'])}：{comments.get(pid) or '暂无'}")
        heading('模块结果')
        state = {'passed': '通过', 'skipped': '直接考通过', 'failed': '未通过'}
        for module in report['modules']:
            line(f"{module['title']}：{state.get(module['state'], module['state'])}")
        profile = report.get('teacher_profile') or {}
        if profile.get('check') or profile.get('text'):
            heading('老师的观察')
            if profile.get('check'):
                line(profile['check'])
            if profile.get('text'):
                line(profile['text'])
    finally:
        painter.end()
