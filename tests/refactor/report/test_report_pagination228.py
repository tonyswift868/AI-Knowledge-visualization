"""Render report PDFs and verify long lines are actually visible on a page."""
from __future__ import annotations

import os
from pathlib import Path
import re
from uuid import uuid4
import json
from datetime import date, timedelta

import pytest

ARTIFACTS = Path(os.environ.get(
    "MF_TEST_SCRATCH", r"D:\MentorFlow\.local\test-tmp\pdf228\tests"
)) / "pagination228"


_APP = None


@pytest.fixture(scope="module", autouse=True)
def _windows_qt_app():
    """PDF glyph verification needs real Windows fonts, not offscreen tofu."""
    global _APP
    from PySide6.QtGui import QGuiApplication
    app = QGuiApplication.instance()
    requested = os.environ.get("QT_QPA_PLATFORM", "").casefold()
    if app is not None and app.platformName().casefold() != "windows":
        pytest.skip("PDF228 needs a fresh QT_QPA_PLATFORM=windows process; "
                    "an existing offscreen/minimal Qt application can render tofu")
    if app is None and requested in {"offscreen", "minimal", "vnc"}:
        pytest.skip("PDF228 cannot verify visible glyphs on the offscreen Qt platform")
    _APP = app if app is not None else QGuiApplication([])
    if _APP.platformName().casefold() != "windows":
        _APP = None
        pytest.skip("PDF228 requires the Windows Qt platform plugin")
    yield _APP


def _fixture(count: int):
    points = []
    titles = {}
    comments = {}
    observations = []
    weakest = []
    for index in range(count):
        pid = f"point228-{index:04d}"
        marker = f"OBS228-{index:04d}"
        ending = f"END228-{index:04d}"
        points.append({"point_id": pid, "summary": f"{marker} 对应知识点"})
        titles[pid] = f"TITLE228-{index:04d} 知识点标题"
        # A long but readable record, like the teacher's observations produced
        # from many answer reasons in RED2-MF07-02.
        observations.append(
            f"{marker}：学生能够解释第 {index + 1} 项概念，并结合练习指出容易混淆的条件；"
            f"复习时还应确认其能用自己的话说明适用范围和例外，记录序号 {index + 1}。{ending}"
        )
        comments[pid] = f"COMMENT228-{index:04d} 针对该知识点的具体建议。"
        weakest.append({"point_id": pid, "score": 1})

    report = {
        "plan": {
            "goal": "学习基础概念", "final_goal": "能够独立解释全部概念",
            "start_date": "2026-10-01", "end_date": "2026-10-30",
            "actual_start_date": "2026-10-01", "actual_completion_date": "2026-10-28",
            "credited_minutes": 450, "planned_minutes": 600, "replan_count": 0,
        },
        "questions": [{"question_id": "question228", "module_id": "module228",
                       "passed": True, "check_questions": 1, "check_correct": 1}],
        "modules": [{"module_id": "module228", "title": "模块228标题",
                     "state": "passed"}],
        "points": points,
        "weakest_points": weakest,
        "point_comments": comments,
        "teacher_profile": {"check": "观察核对完成", "text": "\n".join(observations)},
        "live_points": [],
    }
    views = {
        "modules": [{"module_id": "module228", "title": "模块228标题",
                     "mastered": 1, "total": 1}],
        "errors": {"casual": 0, "module": 0, "check": 0},
        "history": [],
    }
    return report, titles, views, observations


def _read_rendered_pages(pdf_path: Path):
    import pypdfium2 as pdfium
    from pypdfium2 import raw as pdfium_raw

    pdf = pdfium.PdfDocument(str(pdf_path))
    pages = []
    try:
        for index in range(len(pdf)):
            page = pdf[index]
            text_page = page.get_textpage()
            image = page.render(scale=1).to_pil().convert("L")
            info = {
                "text": text_page.get_text_range(),  # diagnostic source only; may contain clipped duplicate blocks
                "text_page": text_page,
                "page": page,
                "size": page.get_size(),
                "image": image,
                "visible_chars": [],
                "visible_boxes": [],
            }
            width, height = info["size"]
            for offset in range(text_page.count_chars()):
                codepoint = pdfium_raw.FPDFText_GetUnicode(text_page.raw, offset)
                char = chr(codepoint) if codepoint else ""
                if not char or char.isspace():
                    info["visible_chars"].append(char)
                    info["visible_boxes"].append(None)
                    continue
                box = text_page.get_charbox(offset)
                if box is None:
                    # Some PDF text objects retain content that is clipped out
                    # by the page rectangle. Such text is not reader-visible.
                    continue
                left, bottom, right, top = box
                if left < 0 or bottom < 0 or right > width or top > height or right <= left or top <= bottom:
                    continue
                x0, x1 = max(0, int(left)-1), min(image.width, int(right+1)+1)
                y0, y1 = max(0, int(height-top)-1), min(image.height, int(height-bottom+1)+1)
                if x1 <= x0 or y1 <= y0:
                    continue
                crop = image.crop((x0, y0, x1, y1))
                has_ink = crop.getextrema()[0] < 245
                crop.close()
                if not has_ink:
                    continue
                info["visible_chars"].append(char)
                info["visible_boxes"].append(box)
            info["visible_text"] = "".join(info["visible_chars"])
            pages.append(info)
        return pdf, pages
    except BaseException:
        pdf.close()
        raise


def _marker_positions(page_info, marker: str):
    text = page_info["visible_text"]
    positions = []
    start = 0
    while True:
        offset = text.find(marker, start)
        if offset < 0:
            break
        boxes = page_info["visible_boxes"][offset:offset+len(marker)]
        assert all(box is not None for box in boxes), f"{marker} includes a non-rendered glyph"
        positions.append((offset, boxes))
        start = offset + len(marker)
    return positions


def _marker_box(page_info, marker: str, search_from: int = 0):
    positions = [(offset, boxes) for offset, boxes in _marker_positions(page_info, marker)
                 if offset >= search_from]
    assert positions, f"{marker} is absent from visible page text"
    boxes = positions[0][1]
    left = min(box[0] for box in boxes)
    bottom = min(box[1] for box in boxes)
    right = max(box[2] for box in boxes)
    top = max(box[3] for box in boxes)
    width, height = page_info["size"]
    assert left >= 0 and bottom >= 0 and right <= width and top <= height, (
        f"{marker} was drawn outside the printable page: {(left, bottom, right, top)} "
        f"on {(width, height)}"
    )

    # Verify actual rendered ink inside the marker bounds. The PDF text layer
    # alone includes glyphs that QPainter may have clipped past the page edge.
    image = page_info["image"]
    x0 = max(0, int(left) - 1)
    x1 = min(image.width, int(right + 1) + 1)
    y0 = max(0, int(height - top) - 1)
    y1 = min(image.height, int(height - bottom + 1) + 1)
    crop = image.crop((x0, y0, x1, y1))
    assert crop.width > 0 and crop.height > 0 and crop.getextrema()[0] < 230, (
        f"{marker} has PDF text coordinates but no visible page ink"
    )
    return (left, bottom, right, top)


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _visible_page_indices(pages, marker):
    found = []
    for page_index, page_info in enumerate(pages):
        for _offset, _boxes in _marker_positions(page_info, marker):
            _marker_box(page_info, marker, _offset)
            found.append(page_index)
    return found


def _assert_characters_on_page(pages):
    """Require visible characters to have complete page boxes and raster ink."""
    checked = sum(box is not None for page in pages for box in page["visible_boxes"])
    assert checked > 0


def _assert_marker_rows_do_not_overlap(pages, markers):
    by_page = {}
    for marker in markers:
        for page_index in set(_visible_page_indices(pages, marker)):
            for start, _boxes in _marker_positions(pages[page_index], marker):
                box = _marker_box(pages[page_index], marker, start)
                by_page.setdefault(page_index, []).append((marker, box))
    for page_index, rows in by_page.items():
        for index, (left_name, left) in enumerate(rows):
            for right_name, right in rows[index + 1:]:
                intersects = min(left[2], right[2]) > max(left[0], right[0]) and min(left[3], right[3]) > max(left[1], right[1])
                assert not intersects, (
                    f"marker rows overlap on page {page_index}: {left_name} / {right_name}"
                )


def _export_and_check(count: int, *, save_last_pages: bool = False):
    from refactor.learning.course.report_pdf import render_report_pdf

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    report, titles, views, observations = _fixture(count)
    pdf_path = ARTIFACTS / f"teacher-observations-228-{count}.pdf"
    assert _APP is not None
    render_report_pdf(report, titles, views, pdf_path)

    pdf, pages = _read_rendered_pages(pdf_path)
    located = {}
    glyph_check_count = sum(box is not None for page in pages for box in page["visible_boxes"])
    try:
        all_text = _normalize("\n".join(page["visible_text"] for page in pages))
        for index, entry in enumerate(observations):
            start_marker = f"OBS228-{index:04d}"
            end_marker = f"END228-{index:04d}"
            normalized_entry = _normalize(entry)
            assert normalized_entry in all_text, (
                f"observation {index} lost or clipped characters between its boundary markers"
            )
            start_pages = _visible_page_indices(pages, start_marker)
            end_pages = _visible_page_indices(pages, end_marker)
            assert len(start_pages) == len(end_pages) == 1, (
                f"observation {index} start/end markers are missing or duplicated"
            )
            start_page, end_page = start_pages[0], end_pages[0]
            assert start_page <= end_page, f"observation {index} is out of reading order"
            start_box = _marker_box(pages[start_page], start_marker)
            located[index] = (start_page, start_box)

        metrics = {
            "expected_records": count,
            "fully_visible_records": len(located),
            "glyph_check_count": glyph_check_count,
            "page_count": len(pages),
            "rendered_page_count": len(pages),
        }
        (ARTIFACTS / f"metrics-{count}.json").write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        _assert_characters_on_page(pages)

        assert len(located) == count
        # Marker rectangles for neighboring records must not overlap, even if
        # the PDF text layer contains both strings.
        _assert_marker_rows_do_not_overlap(
            pages, [f"OBS228-{index:04d}" for index in range(count)]
        )

        if save_last_pages:
            for page_index in range(max(0, len(pages) - 2), len(pages)):
                png = ARTIFACTS / f"long-report-last-{len(pages) - page_index}-pages228.png"
                pages[page_index]["page"].render(scale=1.5).to_pil().save(str(png))
                assert png.is_file() and png.stat().st_size > 0
        return len(pages), located
    finally:
        metrics = {
            "expected_records": count,
            "fully_visible_records": len(located),
            "glyph_check_count": glyph_check_count,
            "page_count": len(pages),
            "rendered_page_count": len(pages),
        }
        (ARTIFACTS / f"metrics-{count}.json").write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        for page_info in pages:
            page_info["image"].close()
        pdf.close()


@pytest.mark.parametrize("count", [1, 30, 40, 60, 80, 100, 300])
def test_report_observations_are_visible_unclipped_and_unoverlapped(count):
    page_count, located = _export_and_check(count, save_last_pages=count == 300)
    assert page_count >= 1
    assert len(located) == count


def test_other_long_report_sections_use_visible_page_space():
    from refactor.learning.course.report_pdf import render_report_pdf

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    report, titles, views, _ = _fixture(180)
    report["plan"]["goal"] = "GOAL228-START " + "课程目标描述" * 180 + " GOAL228-END"
    report["plan"]["final_goal"] = "FINAL228-START " + "学习成果要求" * 180 + " FINAL228-END"
    report["modules"] = [{"module_id": f"module-{i}",
                          "title": f"MODULE228-{i:03d}-START " + "模块说明" * 12 + f" MODULE228-{i:03d}-END",
                          "state": "passed"} for i in range(45)]
    report["points"] = [{"point_id": f"other-point-{i}", "summary": f"概念{i}",
                         "casual_errors": 0, "module_errors": 0} for i in range(180)]
    report["weakest_points"] = [{"point_id": f"other-point-{i}", "score": 1}
                                 for i in range(10)]
    report["point_comments"] = {
        f"other-point-{i}": f"COMMENT228-{i:03d}-START " + "复习建议及易错条件" * 15
        + f" COMMENT228-{i:03d}-END" for i in range(180)
    }
    titles.update({f"other-point-{i}": f"TITLE228-{i:03d}" for i in range(180)})
    report["live_points"] = [{"point_id": f"other-point-{i}",
                              "title": f"LIVE228-{i:03d}-START " + "课堂现场讲解知识点" * 8
                              + f" LIVE228-{i:03d}-END"} for i in range(90)]
    first_day = date(2027, 1, 1)
    history = [{"day": (first_day + timedelta(days=i)).isoformat(), "target_minutes": 30,
         "credited_confirmed_minutes": 20, "credited_tentative_minutes": 0,
         "segment": "whole", "frozen": True} for i in range(180)]
    report["time"] = {"through_date": history[-1]["day"], "days": history}
    views["modules"] = [{"module_id": row["module_id"], "title": row["title"],
                         "mastered": 1, "total": 1} for row in report["modules"]]
    views["history"] = [{"day": row["day"], "target_minutes": row["target_minutes"],
                         "actual_minutes": 20, "debt_minutes": 10, "ahead_minutes": 0}
                        for row in report["time"]["days"]]

    pdf_path = ARTIFACTS / "long-other-sections-228.pdf"
    assert _APP is not None
    render_report_pdf(report, titles, views, pdf_path)
    pdf, pages = _read_rendered_pages(pdf_path)
    try:
        markers = ["GOAL228-START", "GOAL228-END", "FINAL228-START", "FINAL228-END"]
        markers += [f"MODULE228-{i:03d}-{side}" for i in range(45) for side in ("START", "END")]
        markers += [f"TITLE228-{i:03d}" for i in range(10)]
        markers += [f"COMMENT228-{i:03d}-{side}" for i in range(180) for side in ("START", "END")]
        markers += [f"LIVE228-{i:03d}-{side}" for i in range(90) for side in ("START", "END")]
        markers += [row["day"] for row in history]
        for marker in markers:
            visible = []
            for page_index, page_info in enumerate(pages):
                for offset, _boxes in _marker_positions(page_info, marker):
                    _marker_box(page_info, marker, offset)
                    visible.append(page_index)
            expected_occurrences = 2 if marker.startswith("MODULE228-") else 1
            if marker.startswith("TITLE228-"):
                expected_occurrences = 2  # weakest-points line and point-comment line
            assert len(visible) == expected_occurrences, (
                f"{marker} should be visibly rendered {expected_occurrences} times"
            )
        _assert_marker_rows_do_not_overlap(
            pages,
            [f"COMMENT228-{i:03d}-START" for i in range(180)]
            + [f"LIVE228-{i:03d}-START" for i in range(90)]
            + [row["day"] for row in history],
        )
        _assert_characters_on_page(pages)
        assert len(pages) > 5, "long report sections did not paginate"
    finally:
        for page_info in pages:
            page_info["image"].close()
        pdf.close()


def test_vector_charts_render_and_missing_output_path_raises():
    from refactor.learning.course.report_pdf import render_report_pdf

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    report, titles, views, _ = _fixture(1)
    views["modules"] = [{"module_id": "module228", "title": "图表测试模块",
                         "mastered": 1, "total": 4}]
    views["errors"] = {"casual": 1, "module": 0, "check": 0}
    views["history"] = [{"day": "2027-01-01", "target_minutes": 30,
                         "actual_minutes": 20, "debt_minutes": 10, "ahead_minutes": 0}]
    pdf_path = ARTIFACTS / "vector-charts-228.pdf"
    render_report_pdf(report, titles, views, pdf_path)
    pdf, pages = _read_rendered_pages(pdf_path)
    try:
        assert len(pages) == 1
        image = pages[0]["page"].render(scale=1).to_pil().convert("RGB")
        pixels = image.get_flattened_data()
        colors = {
            "blue": (36, 102, 164),
            "rose": (174, 71, 91),
            "gray": (230, 236, 242),
        }
        for name, target in colors.items():
            count = sum(
                1 for pixel in pixels
                if all(abs(pixel[channel] - target[channel]) <= 3 for channel in range(3))
            )
            assert count >= 20, f"{name} vector chart was not visibly painted ({count} pixels)"
        image.close()
    finally:
        for page_info in pages:
            page_info["image"].close()
        pdf.close()

    missing = ARTIFACTS / f"missing-parent-{uuid4().hex}" / "unwritable.pdf"
    with pytest.raises(OSError, match="无法创建 PDF"):
        render_report_pdf(report, titles, views, missing)
