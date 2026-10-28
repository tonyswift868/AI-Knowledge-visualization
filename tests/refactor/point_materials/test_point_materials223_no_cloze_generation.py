"""223: missing source exercises leave concept subpoints study-only."""
import pytest

from refactor.learning.course import point_materials as pm, question_bank as qb
from tests.refactor.point_materials.test_subpoint114_questions import store


def test_point_materials223_no_original_exercise_means_no_question(store):
    point=store.records("points","p")[0]
    result=pm.prepare_point(store,"p",point,dict(
        finder=lambda *a,**k:pytest.fail("must not request cloze candidates"),
        distractor_checker=lambda *a,**k:pytest.fail("must not review generated questions")))
    assert result["questions"]==0 and result["skipped"]==3
    assert "cloze" not in result
    summary=pm.preparation_summary(store,"p","m")
    assert summary["questions"]==0 and "cloze_points" not in summary and "cloze_heavy" not in summary
    assert qb.assessment_questions(store.records("question_bank","p"))==[]
    assert all(row["status"]=="rejected" for row in
        store.records("points","p")[0]["subpoint_preparation"]["processed_subpoints"].values())


def test_point_materials223_original_exercise_still_enters_bank(store):
    point=store.records("points","p")[0]
    point["subpoints"][0]["chunk_ids"]=["found"]
    point["chunk_ids"]=list(dict.fromkeys(cid for sub in point["subpoints"] for cid in sub["chunk_ids"]))
    with store.transaction():store._put("points","p",point)
    result=pm.prepare_point(store,"p",point,{})
    assert result["questions"]==1 and result["skipped"]==2
    question=store.records("question_bank","p")[0]
    assert question["origin"]!="cloze" and question["source"]["chunk_ids"]==["found"]
