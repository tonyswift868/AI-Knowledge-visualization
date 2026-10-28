"""Local vector PDF rendering with Qt's paginated rich-text layout."""
from __future__ import annotations

from pathlib import Path


def render_report_pdf(report, titles, views, path: Path):
    from PySide6.QtCore import QFile, QIODevice, QRectF, QSizeF
    from PySide6.QtGui import (QColor, QFont, QFontDatabase, QPdfWriter, QPageSize,
                              QTextBlockFormat, QTextCharFormat, QTextCursor,
                              QTextDocument, QTextFormat, QPyTextObject, QTextOption)

    output = QFile(str(path))
    if not output.open(QIODevice.OpenModeFlag.WriteOnly):
        raise OSError('无法创建 PDF：' + output.errorString())
    writer = QPdfWriter(output)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setResolution(96)
    writer.setTitle('学习报告：' + report['plan']['goal'])
    families = set(QFontDatabase.families())
    family = next((f for f in ('Microsoft YaHei', 'Noto Sans CJK SC', 'SimSun', 'Arial Unicode MS')
                   if f in families), QFont().family())
    margin = 46
    usable = writer.width() - 2 * margin
    ink, muted, blue, rose = QColor('#182536'), QColor('#526275'), QColor('#2466a4'), QColor('#ae475b')

    document = QTextDocument()
    document.setUndoRedoEnabled(False)
    document.setDefaultFont(QFont(family, 10))
    document.documentLayout().setPaintDevice(writer)
    document.setDocumentMargin(margin)
    document.setPageSize(QSizeF(writer.width(), writer.height()))
    option = document.defaultTextOption()
    option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    document.setDefaultTextOption(option)
    cursor = QTextCursor(document)

    # Charts remain vector objects. Qt places their intrinsic height alongside
    # the text; neither charts nor paragraphs decide where a page ends.
    chart_type = QTextFormat.ObjectTypes.UserObject + 1
    values_property = QTextFormat.Property.UserProperty
    color_property = values_property + 1

    class Bars(QPyTextObject):
        def intrinsicSize(self, _document, _position, fmt):
            return QSizeF(usable * .56, 14 if len(fmt.property(values_property)) == 1 else 10)

        def drawObject(self, painter, rect, _document, _position, fmt):
            values = fmt.property(values_property)
            if len(values) == 1:
                track = QRectF(rect.x(), rect.y(), rect.width(), 11)
                painter.fillRect(track, QColor('#e6ecf2'))
                painter.fillRect(QRectF(track.x(), track.y(), track.width() * values[0], 11),
                                 fmt.property(color_property))
            else:
                for index, (value, color) in enumerate(zip(values, (blue, rose))):
                    painter.fillRect(QRectF(rect.x(), rect.y() + index * 5, rect.width() * value, 3), color)

    bars = Bars(document)
    document.documentLayout().registerHandler(chart_type, bars)

    def block(*, before=0, after=7, indent=0):
        fmt = QTextBlockFormat()
        fmt.setTopMargin(before)
        fmt.setBottomMargin(after)
        fmt.setLeftMargin(indent)
        if cursor.atStart():
            cursor.setBlockFormat(fmt)
        else:
            cursor.insertBlock(fmt)

    def line(value, *, size=10, bold=False, color=ink, before=0, after=7):
        block(before=before, after=after)
        fmt = QTextCharFormat()
        font = QFont(family, size)
        font.setBold(bold)
        fmt.setFont(font)
        fmt.setForeground(color)
        # Plain text (also literal HTML) can span many pages. Qt splits its
        # lines at page boundaries instead of painting beyond a fixed rect.
        cursor.insertText(str(value), fmt)

    def heading(value):
        line(value, size=14, bold=True, color=blue, before=13)

    def chart(values, color=blue):
        block(after=7, indent=110)
        fmt = QTextCharFormat()
        fmt.setObjectType(chart_type)
        fmt.setProperty(values_property, values)
        fmt.setProperty(color_property, color)
        cursor.insertText('\ufffc', fmt)

    def bar(label, value, total, color=blue, *, count=False):
        label_value = f'{value} 次' if count else f'{value}/{total}'
        line(f'{label}  {label_value}', size=9, after=3)
        chart([min(1, value / max(1, total))], color)

    def daily(row):
        label = f"{row['day']}  目标 {row['target_minutes']}  实际 {row['actual_minutes']}"
        line(label + f"  比当天目标少 {row['debt_minutes']}  多 {row['ahead_minutes']}", size=9, after=3)
        limit = max(1, row['target_minutes'], row['actual_minutes'])
        chart([row['target_minutes'] / limit, row['actual_minutes'] / limit])

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
        # With pageSize set, Qt prints its pagination as-is. Keep the existing
        # margins/title styles without adding Qt's default page-number footer.
        document.print_(writer)
    finally:
        document.documentLayout().setPaintDevice(None)
        # Finish the PDF trailer before closing its output device.
        del writer
        output.close()
